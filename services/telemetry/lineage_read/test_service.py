#!/usr/bin/env python3
"""
Unit tests for the LIN-002 Lineage Read Service.

Covers:
- Graph node/edge operations
- Iterative BFS traversal (no recursion)
- Projection builders for all 4 query families
- Conflict marker detection (rollback, alias drift)
- Corpus loading
- Benchmark SLA validation
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Add project root to path for imports
_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from services.telemetry.lineage_read.service import (
    LineageGraph,
    LineageTraverser,
    LineageReadService,
    ProjectionBuilder,
    CorpusLoader,
    GraphEdge,
    ProjectionResult,
    NODE_SOURCE_RECORD,
    NODE_STRATEGY_SPEC,
    NODE_EXPERIMENT_RUN,
    NODE_CANDIDATE_ARTIFACT,
    NODE_APPROVAL_DECISION,
    NODE_CAPITAL_POOL,
    NODE_PERSONA_BINDING,
    NODE_DEPLOYMENT_PLAN,
    NODE_RUNTIME_BINDING,
    NODE_TELEMETRY_EVENT,
    NODE_BROKER_ORDER_EVENT,
    NODE_EVOLUTION_DECISION,
    EDGE_RUNTIME_PLAN,
    EDGE_RUNTIME_POOL,
    EDGE_RUNTIME_PERSONA,
    EDGE_RUNTIME_ROLLBACK,
    EDGE_TELEMETRY_BINDING,
    EDGE_TELEMETRY_PLAN,
    EDGE_TELEMETRY_POOL,
    EDGE_TELEMETRY_PERSONA,
    EDGE_DEPLOYMENT_POOL,
    EDGE_DEPLOYMENT_PERSONA,
)


# Minimal test corpus
_MINIMAL_CORPUS = {
    "metadata": {
        "task_id": "LIN-002-TEST",
        "projection_updated_at": "2026-04-10T00:00:00Z",
    },
    "node_sets": {
        "capital_pools": [{"pool_id": "pool-1", "name": "Test Pool", "status": "active", "single_runtime_enforced": True, "created_at": "2026-04-10T00:00:00Z"}],
        "persona_capital_bindings": [{"binding_id": "pb-1", "persona_id": "p-1", "capital_pool_id": "pool-1", "status": "active", "created_at": "2026-04-10T00:00:00Z"}],
        "deployment_plans": [{"plan_id": "plan-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "capital_pool_id": "pool-1", "binding_id": "pb-1", "current_stage": "live", "status": "executed", "created_at": "2026-04-10T00:00:00Z"}],
        "runtime_bindings": [
            {"binding_id": "rb-1", "runtime_id": "rt-1", "capital_pool_id": "pool-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "deployment_mode": "live", "plan_id": "plan-1", "persona_capital_binding_id": "pb-1", "status": "active", "effective_at": "2026-04-10T00:00:00Z"},
            {"binding_id": "rb-2", "runtime_id": "rt-1", "capital_pool_id": "pool-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "deployment_mode": "live", "plan_id": "plan-1", "persona_capital_binding_id": "pb-1", "status": "active", "effective_at": "2026-04-10T00:01:00Z", "rollback_parent": "rb-1", "rollback_action_type": "replace"},
        ],
        "telemetry_events": [
            {"event_id": "evt-1", "event_type": "pnl_snapshot", "binding_id": "rb-1", "plan_id": "plan-1", "capital_pool_id": "pool-1", "persona_capital_binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "runtime_id": "rt-1", "event_produced_at": "2026-04-10T00:00:30Z"},
        ],
    },
    "query_families": [],
    "benchmark_cases": [],
}


_SOURCE_RUNTIME_TRACE_CORPUS = {
    "metadata": {
        "task_id": "SD-LIN-TRACE-001-TEST",
        "projection_updated_at": "2026-04-27T12:00:00Z",
    },
    "node_sets": {
        "source_records": [
            {
                "source_id": "src-alpha",
                "source_type": "research_note",
                "created_at": "2026-04-27T11:00:00Z",
            }
        ],
        "strategy_specs": [
            {
                "strategy_id": "strategy-alpha",
                "source_id": "src-alpha",
                "created_at": "2026-04-27T11:01:00Z",
            }
        ],
        "experiment_runs": [
            {
                "run_id": "run-alpha",
                "strategy_id": "strategy-alpha",
                "created_at": "2026-04-27T11:02:00Z",
            }
        ],
        "candidate_artifacts": [
            {
                "artifact_id": "artifact-alpha",
                "artifact_version": "1.0.0",
                "artifact_type": "strategy_package",
                "run_id": "run-alpha",
                "created_at": "2026-04-27T11:03:00Z",
            }
        ],
        "approval_decisions": [
            {
                "decision_id": "approval-alpha",
                "target_id": "artifact-alpha",
                "decision_state": "approved",
                "created_at": "2026-04-27T11:04:00Z",
            }
        ],
        "capital_pools": [
            {
                "pool_id": "pool-alpha",
                "single_runtime_enforced": True,
                "created_at": "2026-04-27T11:05:00Z",
            }
        ],
        "persona_capital_bindings": [
            {
                "binding_id": "pcb-alpha",
                "capital_pool_id": "pool-alpha",
                "created_at": "2026-04-27T11:06:00Z",
            }
        ],
        "deployment_plans": [
            {
                "plan_id": "plan-alpha",
                "approval_decision_id": "approval-alpha",
                "artifact_id": "artifact-alpha",
                "artifact_version": "1.0.0",
                "strategy_id": "strategy-alpha",
                "capital_pool_id": "pool-alpha",
                "binding_id": "pcb-alpha",
                "target_stage": "canary",
                "status": "executed",
                "created_at": "2026-04-27T11:07:00Z",
            }
        ],
        "runtime_bindings": [
            {
                "binding_id": "rb-alpha",
                "runtime_id": "runtime-alpha",
                "capital_pool_id": "pool-alpha",
                "artifact_id": "artifact-alpha",
                "artifact_version": "1.0.0",
                "deployment_mode": "canary",
                "plan_id": "plan-alpha",
                "persona_capital_binding_id": "pcb-alpha",
                "status": "active",
                "effective_at": "2026-04-27T11:08:00Z",
            }
        ],
        "telemetry_events": [
            {
                "event_id": "evt-alpha-pnl",
                "event_type": "pnl_snapshot",
                "binding_id": "rb-alpha",
                "runtime_id": "runtime-alpha",
                "capital_pool_id": "pool-alpha",
                "artifact_id": "artifact-alpha",
                "artifact_version": "1.0.0",
                "deployment_stage": "canary",
                "plan_id": "plan-alpha",
                "persona_capital_binding_id": "pcb-alpha",
                "event_produced_at": "2026-04-27T11:09:00Z",
                "trace_id": "trace-alpha",
                "request_id": "req-alpha-1",
                "strategy_id": "strategy-alpha",
                "registry_id": "registry-alpha",
                "metrics": {"pnl": 42.0},
            },
            {
                "event_id": "evt-alpha-fill",
                "event_type": "fill_observation",
                "binding_id": "rb-alpha",
                "runtime_id": "runtime-alpha",
                "capital_pool_id": "pool-alpha",
                "artifact_id": "artifact-alpha",
                "artifact_version": "1.0.0",
                "deployment_stage": "canary",
                "plan_id": "plan-alpha",
                "persona_capital_binding_id": "pcb-alpha",
                "event_produced_at": "2026-04-27T11:10:00Z",
                "trace_id": "trace-alpha",
                "request_id": "req-alpha-2",
                "strategy_id": "strategy-alpha",
                "registry_id": "registry-alpha",
                "broker": "paper_broker",
                "order_id": "order-alpha-1",
                "metrics": {"fill_quantity": 3, "fill_price": 101.25},
            },
        ],
        "broker_order_events": [
            {
                "order_event_id": "boe-alpha-fill",
                "order_id": "order-alpha-1",
                "order_status": "filled",
                "broker": "paper_broker",
                "trace_id": "trace-alpha",
                "runtime_binding_id": "rb-alpha",
                "deployment_plan_id": "plan-alpha",
                "telemetry_event_id": "evt-alpha-fill",
                "created_at": "2026-04-27T11:10:01Z",
            }
        ],
        "incident_cases": [
            {
                "incident_id": "inc-alpha",
                "binding_id": "rb-alpha",
                "telemetry_event_ids": ["evt-alpha-fill"],
                "created_at": "2026-04-27T11:11:00Z",
            }
        ],
        "postmortems": [
            {
                "postmortem_id": "pm-alpha",
                "incident_id": "inc-alpha",
                "created_at": "2026-04-27T11:12:00Z",
            }
        ],
        "evolution_decisions": [
            {
                "decision_id": "evo-alpha",
                "target_type": "candidate_artifact",
                "target_id": "artifact-alpha",
                "target_version": "1.0.0",
                "action_type": "revalidate",
                "decision_state": "approved",
                "linked_incident_id": "inc-alpha",
                "linked_postmortem_id": "pm-alpha",
                "evidence_refs": [{"ref_type": "telemetry_summary", "ref_id": "trace-alpha"}],
                "created_at": "2026-04-27T11:13:00Z",
            }
        ],
    },
    "query_families": [],
    "benchmark_cases": [],
}


class TestLineageGraph(unittest.TestCase):
    def test_add_and_get_node(self):
        g = LineageGraph()
        g.add_node("foo", "id-1", {"key": "val"})
        node = g.get_node("foo", "id-1")
        self.assertIsNotNone(node)
        self.assertEqual(node.node_type, "foo")
        self.assertEqual(node.node_id, "id-1")
        self.assertEqual(node.data["key"], "val")

    def test_get_missing_node(self):
        g = LineageGraph()
        self.assertIsNone(g.get_node("foo", "missing"))

    def test_add_edge_and_traverse(self):
        g = LineageGraph()
        g.add_node("A", "a1", {})
        g.add_node("B", "b1", {})
        edge = GraphEdge(edge_type="a_to_b", from_type="A", from_id="a1", to_type="B", to_id="b1")
        g.add_edge(edge)

        outgoing = g.outgoing("a_to_b", "A", "a1")
        self.assertEqual(len(outgoing), 1)
        self.assertEqual(outgoing[0].to_id, "b1")

        incoming = g.incoming("a_to_b", "B", "b1")
        self.assertEqual(len(incoming), 1)
        self.assertEqual(incoming[0].from_id, "a1")

    def test_nodes_by_type(self):
        g = LineageGraph()
        g.add_node("A", "a1", {})
        g.add_node("A", "a2", {})
        g.add_node("B", "b1", {})
        self.assertEqual(len(g.nodes_by_type("A")), 2)
        self.assertEqual(len(g.nodes_by_type("B")), 1)


class TestCorpusLoader(unittest.TestCase):
    def test_load_minimal_corpus(self):
        graph = CorpusLoader.load(_MINIMAL_CORPUS)
        # Check node counts
        self.assertEqual(len(graph.nodes_by_type(NODE_CAPITAL_POOL)), 1)
        self.assertEqual(len(graph.nodes_by_type(NODE_PERSONA_BINDING)), 1)
        self.assertEqual(len(graph.nodes_by_type(NODE_DEPLOYMENT_PLAN)), 1)
        self.assertEqual(len(graph.nodes_by_type(NODE_RUNTIME_BINDING)), 2)
        self.assertEqual(len(graph.nodes_by_type(NODE_TELEMETRY_EVENT)), 1)

    def test_edges_created(self):
        graph = CorpusLoader.load(_MINIMAL_CORPUS)
        # Check telemetry event -> runtime binding edge
        edges = graph.incoming(EDGE_TELEMETRY_BINDING, NODE_RUNTIME_BINDING, "rb-1")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].from_id, "evt-1")

        # Check rollback self-lineage edge
        edges = graph.outgoing(EDGE_RUNTIME_ROLLBACK, NODE_RUNTIME_BINDING, "rb-2")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].to_id, "rb-1")


class TestLineageTraverser(unittest.TestCase):
    def setUp(self):
        self.graph = CorpusLoader.load(_MINIMAL_CORPUS)
        self.traverser = LineageTraverser(self.graph)

    def test_traverse_from_runtime_binding(self):
        result = self.traverser.traverse_from(NODE_RUNTIME_BINDING, "rb-1")
        self.assertEqual(result.target_type, NODE_RUNTIME_BINDING)
        self.assertEqual(result.target_id, "rb-1")
        # Should find upstream nodes (pool, plan, persona binding)
        self.assertGreater(len(result.upstream_chain), 0)
        # Should find downstream telemetry events
        self.assertGreater(len(result.downstream_chain), 0)

    def test_traverse_from_telemetry_event(self):
        result = self.traverser.traverse_from(NODE_TELEMETRY_EVENT, "evt-1")
        self.assertEqual(result.target_type, NODE_TELEMETRY_EVENT)
        # Should find upstream nodes (binding, plan, pool, persona)
        self.assertGreater(len(result.upstream_chain), 0)

    def test_traverse_from_missing_node(self):
        result = self.traverser.traverse_from(NODE_RUNTIME_BINDING, "nonexistent")
        self.assertEqual(result.conflict_markers[0]["code"], "node_not_found")

    def test_rollback_conflict_detection(self):
        result = self.traverser.traverse_from(NODE_RUNTIME_BINDING, "rb-2")
        rollback_markers = [c for c in result.conflict_markers if c.get("code") == "rollback_parent"]
        self.assertGreater(len(rollback_markers), 0)
        self.assertEqual(rollback_markers[0]["id"], "rb-1")


class TestProjectionBuilder(unittest.TestCase):
    def setUp(self):
        self.graph = CorpusLoader.load(_MINIMAL_CORPUS)
        self.traverser = LineageTraverser(self.graph)

    def test_runtime_binding_projection(self):
        result = ProjectionBuilder.runtime_binding_projection(self.traverser, "rb-1")
        self.assertEqual(result["target_type"], NODE_RUNTIME_BINDING)
        self.assertEqual(result["target_id"], "rb-1")
        # Should have artifact_ref in upstream
        artifact_refs = [i for i in result["upstream_chain"] if i.get("type") == "artifact_ref"]
        self.assertEqual(len(artifact_refs), 1)
        self.assertEqual(artifact_refs[0]["id"], "art-1@1.0.0")
        # Should have runtime_ref in downstream
        runtime_refs = [i for i in result["downstream_chain"] if i.get("type") == "runtime_ref"]
        self.assertEqual(len(runtime_refs), 1)
        self.assertEqual(runtime_refs[0]["id"], "rt-1")

    def test_capital_pool_projection(self):
        result = ProjectionBuilder.capital_pool_projection(self.traverser, "pool-1")
        self.assertEqual(result["target_type"], NODE_CAPITAL_POOL)
        self.assertEqual(result["target_id"], "pool-1")
        # Pool should be in upstream chain
        pool_items = [i for i in result["upstream_chain"] if i.get("type") == NODE_CAPITAL_POOL]
        self.assertGreater(len(pool_items), 0)
        # Should have runtime bindings in downstream
        rb_items = [i for i in result["downstream_chain"] if i.get("type") == NODE_RUNTIME_BINDING]
        self.assertEqual(len(rb_items), 2)

    def test_telemetry_event_trace(self):
        result = ProjectionBuilder.telemetry_event_trace(self.traverser, "evt-1")
        self.assertEqual(result["target_type"], NODE_TELEMETRY_EVENT)
        self.assertEqual(result["target_id"], "evt-1")
        # Should have artifact_ref
        artifact_refs = [i for i in result["upstream_chain"] if i.get("type") == "artifact_ref"]
        self.assertEqual(len(artifact_refs), 1)

    def test_forensic_plan_trace(self):
        result = ProjectionBuilder.forensic_plan_trace(self.traverser, "plan-1")
        self.assertEqual(result["target_type"], NODE_DEPLOYMENT_PLAN)
        self.assertEqual(result["target_id"], "plan-1")
        # Should have plan in upstream
        plan_items = [i for i in result["upstream_chain"] if i.get("type") == NODE_DEPLOYMENT_PLAN]
        self.assertGreater(len(plan_items), 0)
        # Should have artifact_ref
        artifact_refs = [i for i in result["upstream_chain"] if i.get("type") == "artifact_ref"]
        self.assertEqual(len(artifact_refs), 1)
        # Should have rollback markers for rb-2
        rollback_markers = [c for c in result["conflict_markers"] if c.get("code") == "rollback_parent"]
        self.assertGreater(len(rollback_markers), 0)


class TestLineageReadService(unittest.TestCase):
    def setUp(self):
        self.service = LineageReadService()
        self.service.load_corpus(_MINIMAL_CORPUS)

    def test_query_runtime_binding(self):
        result = self.service.query("runtime_binding_projection", binding_id="rb-1")
        self.assertEqual(result["target_id"], "rb-1")

    def test_query_capital_pool(self):
        result = self.service.query("capital_pool_projection", pool_id="pool-1")
        self.assertEqual(result["target_id"], "pool-1")

    def test_query_telemetry_event(self):
        result = self.service.query("telemetry_event_trace", event_id="evt-1")
        self.assertEqual(result["target_id"], "evt-1")

    def test_query_forensic_plan(self):
        result = self.service.query("forensic_plan_trace", plan_id="plan-1")
        self.assertEqual(result["target_id"], "plan-1")

    def test_query_source_runtime_telemetry_trace(self):
        svc = LineageReadService()
        svc.load_corpus(_SOURCE_RUNTIME_TRACE_CORPUS)

        result = svc.query("source_runtime_telemetry_trace", trace_id="trace-alpha")

        self.assertEqual(result["target_type"], "trace")
        self.assertEqual(result["target_id"], "trace-alpha")
        self.assertIs(result["derived_only"], True)
        self.assertEqual(result["missing_edges"], [])

        source_ids = [item["id"] for item in result["operator_trace"]["source_chain"]]
        self.assertEqual(
            source_ids,
            [
                "src-alpha",
                "strategy-alpha",
                "run-alpha",
                "artifact-alpha",
                "artifact-alpha@1.0.0",
            ],
        )

        deployment_ids = [item["id"] for item in result["operator_trace"]["deployment_chain"]]
        self.assertIn("approval-alpha", deployment_ids)
        self.assertIn("plan-alpha", deployment_ids)
        self.assertIn("pool-alpha", deployment_ids)
        self.assertIn("pcb-alpha", deployment_ids)

        runtime_ids = [item["id"] for item in result["operator_trace"]["runtime_chain"]]
        self.assertEqual(runtime_ids, ["rb-alpha", "runtime-alpha"])

        lifecycle_ids = [item["id"] for item in result["operator_trace"]["broker_order_lifecycle"]]
        self.assertIn("evt-alpha-fill", lifecycle_ids)
        self.assertIn("boe-alpha-fill", lifecycle_ids)

        evolution_ids = [item["id"] for item in result["operator_trace"]["evolution_refs"]]
        self.assertEqual(evolution_ids, ["evo-alpha"])

        refs = result["refs"]
        self.assertEqual(refs["source_record_ids"], ["src-alpha"])
        self.assertEqual(refs["experiment_run_ids"], ["run-alpha"])
        self.assertEqual(refs["approval_decision_ids"], ["approval-alpha"])
        self.assertEqual(refs["runtime_binding_ids"], ["rb-alpha"])
        self.assertEqual(refs["telemetry_event_ids"], ["evt-alpha-fill", "evt-alpha-pnl"])
        self.assertEqual(refs["broker_order_event_ids"], ["boe-alpha-fill"])
        self.assertEqual(refs["broker_order_ids"], ["order-alpha-1"])
        self.assertEqual(refs["incident_ids"], ["inc-alpha"])
        self.assertEqual(refs["postmortem_ids"], ["pm-alpha"])
        self.assertEqual(refs["evolution_decision_ids"], ["evo-alpha"])
        self.assertEqual(refs["trace_ids"], ["trace-alpha"])
        self.assertIn("artifact-alpha@1.0.0", refs["artifact_refs"])

    def test_source_runtime_trace_reconciliation_closure(self):
        corpus = json.loads(json.dumps(_SOURCE_RUNTIME_TRACE_CORPUS))
        corpus["node_sets"]["telemetry_events"].extend(
            [
                {
                    "event_id": "evt-alpha-order-submitted",
                    "event_type": "order_submitted",
                    "binding_id": "rb-alpha",
                    "runtime_id": "runtime-alpha",
                    "capital_pool_id": "pool-alpha",
                    "artifact_id": "artifact-alpha",
                    "artifact_version": "1.0.0",
                    "deployment_stage": "canary",
                    "plan_id": "plan-alpha",
                    "persona_capital_binding_id": "pcb-alpha",
                    "event_produced_at": "2026-04-27T11:09:30Z",
                    "trace_id": "trace-alpha",
                    "strategy_id": "strategy-alpha",
                    "order_id": "order-alpha-2",
                    "order_status": "submitted",
                    "broker": "paper_broker",
                },
                {
                    "event_id": "evt-alpha-order-accepted",
                    "event_type": "order_accepted",
                    "binding_id": "rb-alpha",
                    "runtime_id": "runtime-alpha",
                    "capital_pool_id": "pool-alpha",
                    "artifact_id": "artifact-alpha",
                    "artifact_version": "1.0.0",
                    "deployment_stage": "canary",
                    "plan_id": "plan-alpha",
                    "persona_capital_binding_id": "pcb-alpha",
                    "event_produced_at": "2026-04-27T11:09:40Z",
                    "trace_id": "trace-alpha",
                    "strategy_id": "strategy-alpha",
                    "order_id": "order-alpha-2",
                    "order_status": "accepted",
                    "broker": "paper_broker",
                },
                {
                    "event_id": "evt-alpha-order-partial",
                    "event_type": "order_partially_filled",
                    "binding_id": "rb-alpha",
                    "runtime_id": "runtime-alpha",
                    "capital_pool_id": "pool-alpha",
                    "artifact_id": "artifact-alpha",
                    "artifact_version": "1.0.0",
                    "deployment_stage": "canary",
                    "plan_id": "plan-alpha",
                    "persona_capital_binding_id": "pcb-alpha",
                    "event_produced_at": "2026-04-27T11:10:20Z",
                    "trace_id": "trace-alpha",
                    "strategy_id": "strategy-alpha",
                    "order_id": "order-alpha-2",
                    "order_status": "partially_filled",
                    "fill_status": "partially_filled",
                    "broker": "paper_broker",
                },
                {
                    "event_id": "evt-alpha-order-canceled",
                    "event_type": "order_canceled",
                    "binding_id": "rb-alpha",
                    "runtime_id": "runtime-alpha",
                    "capital_pool_id": "pool-alpha",
                    "artifact_id": "artifact-alpha",
                    "artifact_version": "1.0.0",
                    "deployment_stage": "canary",
                    "plan_id": "plan-alpha",
                    "persona_capital_binding_id": "pcb-alpha",
                    "event_produced_at": "2026-04-27T11:10:40Z",
                    "trace_id": "trace-alpha",
                    "strategy_id": "strategy-alpha",
                    "order_id": "order-alpha-2",
                    "order_status": "canceled",
                    "broker": "paper_broker",
                },
                {
                    "event_id": "evt-alpha-position",
                    "event_type": "position_snapshot",
                    "binding_id": "rb-alpha",
                    "runtime_id": "runtime-alpha",
                    "capital_pool_id": "pool-alpha",
                    "artifact_id": "artifact-alpha",
                    "artifact_version": "1.0.0",
                    "deployment_stage": "canary",
                    "plan_id": "plan-alpha",
                    "persona_capital_binding_id": "pcb-alpha",
                    "event_produced_at": "2026-04-27T11:10:50Z",
                    "trace_id": "trace-alpha",
                    "strategy_id": "strategy-alpha",
                    "position_qty": 0,
                },
            ]
        )
        corpus["node_sets"]["broker_order_events"].extend(
            [
                {
                    "order_event_id": "boe-alpha-submitted",
                    "order_id": "order-alpha-2",
                    "order_status": "submitted",
                    "broker": "paper_broker",
                    "trace_id": "trace-alpha",
                    "runtime_binding_id": "rb-alpha",
                    "deployment_plan_id": "plan-alpha",
                    "telemetry_event_id": "evt-alpha-order-submitted",
                    "created_at": "2026-04-27T11:09:31Z",
                },
                {
                    "order_event_id": "boe-alpha-canceled",
                    "order_id": "order-alpha-2",
                    "order_status": "canceled",
                    "broker": "paper_broker",
                    "trace_id": "trace-alpha",
                    "runtime_binding_id": "rb-alpha",
                    "deployment_plan_id": "plan-alpha",
                    "telemetry_event_id": "evt-alpha-order-canceled",
                    "created_at": "2026-04-27T11:10:41Z",
                },
            ]
        )
        corpus["node_sets"]["position_snapshots"] = [
            {
                "position_snapshot_id": "pos-alpha-flat",
                "runtime_binding_id": "rb-alpha",
                "deployment_plan_id": "plan-alpha",
                "telemetry_event_id": "evt-alpha-position",
                "trace_id": "trace-alpha",
                "symbol": "SPY",
                "position_qty": 0,
                "created_at": "2026-04-27T11:10:51Z",
            }
        ]
        corpus["node_sets"]["reconciliation_runs"] = [
            {
                "recon_run_id": "recon-alpha",
                "recon_type": "order_fill_cancel_position",
                "scope_type": "runtime",
                "scope_id": "rb-alpha",
                "runtime_binding_id": "rb-alpha",
                "deployment_plan_id": "plan-alpha",
                "current_ref": "order-alpha-2",
                "status": "completed",
                "trace_id": "trace-alpha",
                "finished_at": "2026-04-27T11:11:00Z",
            }
        ]
        corpus["node_sets"]["reconciliation_records"] = [
            {
                "record_id": "recon-rec-order-alpha",
                "recon_run_id": "recon-alpha",
                "recon_type": "order_fill_cancel_position",
                "scope_ref": "order-alpha-2",
                "expected_ref": "evt-alpha-order-submitted",
                "actual_ref": "boe-alpha-canceled",
                "status": "pass",
                "severity": "none",
                "evidence_refs": ["evt-alpha-order-canceled", "boe-alpha-canceled"],
                "generated_at": "2026-04-27T11:11:01Z",
            },
            {
                "record_id": "recon-rec-position-alpha",
                "recon_run_id": "recon-alpha",
                "recon_type": "order_fill_cancel_position",
                "scope_ref": "rb-alpha",
                "expected_ref": "evt-alpha-position",
                "actual_ref": "pos-alpha-flat",
                "status": "pass",
                "severity": "none",
                "evidence_refs": ["evt-alpha-position", "pos-alpha-flat"],
                "generated_at": "2026-04-27T11:11:02Z",
            },
        ]
        corpus["node_sets"]["drift_reports"] = [
            {
                "drift_report_id": "drift-alpha",
                "recon_run_id": "recon-alpha",
                "drift_type": "paper_live",
                "scope_ref": "rb-alpha",
                "severity": "low",
                "recommended_action": "observe",
                "status": "closed",
                "evidence_refs": ["recon-rec-order-alpha", "recon-rec-position-alpha"],
                "generated_at": "2026-04-27T11:11:03Z",
            }
        ]
        corpus["node_sets"]["alert_candidates"] = [
            {
                "alert_candidate_id": "alert-alpha",
                "source_type": "drift",
                "source_ref": "drift-alpha",
                "rule_id": "paper_live_drift_closed_v1",
                "severity": "low",
                "scope_ref": "rb-alpha",
                "status": "suppressed",
                "evidence_refs": ["drift-alpha"],
                "created_at": "2026-04-27T11:11:04Z",
            }
        ]

        svc = LineageReadService()
        svc.load_corpus(corpus)

        result = svc.query("source_runtime_telemetry_trace", trace_id="trace-alpha")
        closure = result["operator_trace"]["reconciliation_closure"]

        self.assertEqual(closure["status"], "closed")
        self.assertIs(closure["lifecycle_proof_complete"], True)
        self.assertEqual(closure["proof_gaps"], [])
        self.assertEqual(closure["order_lifecycle"]["order_ids"], ["order-alpha-1", "order-alpha-2"])
        self.assertIs(closure["order_lifecycle"]["has_fill_event"], True)
        self.assertIs(closure["order_lifecycle"]["has_cancel_event"], True)
        self.assertEqual(closure["position_closure"]["latest_snapshot_id"], "pos-alpha-flat")
        self.assertEqual(closure["position_closure"]["latest_position_qty"], 0.0)
        self.assertEqual(closure["reconciliation"]["run_count"], 1)
        self.assertEqual(closure["reconciliation"]["record_count"], 2)
        self.assertEqual(closure["paper_live_drift"]["open_report_count"], 0)
        self.assertEqual(closure["alert_closure"]["open_candidate_count"], 0)

        refs = result["refs"]
        self.assertEqual(refs["position_snapshot_ids"], ["pos-alpha-flat"])
        self.assertEqual(refs["reconciliation_run_ids"], ["recon-alpha"])
        self.assertEqual(
            refs["reconciliation_record_ids"],
            ["recon-rec-order-alpha", "recon-rec-position-alpha"],
        )
        self.assertEqual(refs["drift_report_ids"], ["drift-alpha"])
        self.assertEqual(refs["alert_candidate_ids"], ["alert-alpha"])
        self.assertEqual(result["position_snapshot_count"], 1)
        self.assertEqual(result["reconciliation_run_count"], 1)
        self.assertEqual(result["reconciliation_record_count"], 2)
        self.assertEqual(result["drift_report_count"], 1)
        self.assertEqual(result["alert_candidate_count"], 1)

    def test_source_runtime_trace_uses_position_snapshot_telemetry_event(self):
        corpus = json.loads(json.dumps(_SOURCE_RUNTIME_TRACE_CORPUS))
        corpus["node_sets"]["telemetry_events"].append(
            {
                "event_id": "evt-alpha-position-only",
                "event_type": "position_snapshot",
                "binding_id": "rb-alpha",
                "runtime_id": "runtime-alpha",
                "capital_pool_id": "pool-alpha",
                "artifact_id": "artifact-alpha",
                "artifact_version": "1.0.0",
                "deployment_stage": "canary",
                "plan_id": "plan-alpha",
                "persona_capital_binding_id": "pcb-alpha",
                "event_produced_at": "2026-04-27T11:10:50Z",
                "trace_id": "trace-alpha",
                "strategy_id": "strategy-alpha",
                "symbol": "SPY",
                "position_qty": 0,
            }
        )
        corpus["node_sets"]["reconciliation_runs"] = [
            {
                "recon_run_id": "recon-alpha",
                "recon_type": "order_fill_position",
                "scope_type": "runtime",
                "scope_id": "rb-alpha",
                "runtime_binding_id": "rb-alpha",
                "deployment_plan_id": "plan-alpha",
                "status": "completed",
                "trace_id": "trace-alpha",
                "finished_at": "2026-04-27T11:11:00Z",
            }
        ]
        corpus["node_sets"]["reconciliation_records"] = [
            {
                "record_id": "recon-rec-position-alpha",
                "recon_run_id": "recon-alpha",
                "recon_type": "order_fill_position",
                "scope_ref": "rb-alpha",
                "expected_ref": "evt-alpha-position-only",
                "actual_ref": "evt-alpha-position-only",
                "status": "pass",
                "severity": "none",
                "evidence_refs": ["evt-alpha-position-only"],
                "generated_at": "2026-04-27T11:11:02Z",
            },
        ]

        svc = LineageReadService()
        svc.load_corpus(corpus)

        result = svc.query("source_runtime_telemetry_trace", trace_id="trace-alpha")
        closure = result["operator_trace"]["reconciliation_closure"]

        self.assertEqual(result["position_snapshot_count"], 1)
        self.assertEqual(result["refs"]["position_snapshot_ids"], ["evt-alpha-position-only"])
        self.assertEqual(
            result["operator_trace"]["position_snapshots"][0]["source"],
            "telemetry_event",
        )
        self.assertEqual(
            closure["position_closure"]["latest_snapshot_id"],
            "evt-alpha-position-only",
        )
        self.assertNotIn("missing_position_snapshot", closure["proof_gaps"])
        self.assertEqual(closure["status"], "closed")

    def test_source_runtime_trace_surfaces_missing_edges(self):
        corpus = json.loads(json.dumps(_SOURCE_RUNTIME_TRACE_CORPUS))
        corpus["node_sets"]["source_records"] = []
        svc = LineageReadService()
        svc.load_corpus(corpus)

        result = svc.query("source_runtime_telemetry_trace", trace_id="trace-alpha")

        missing = result["missing_edges"]
        self.assertTrue(
            any(
                item["edge_type"] == "strategy_spec.source_record"
                and item["to_id"] == "src-alpha"
                for item in missing
            ),
            f"Expected missing source edge, got: {missing}",
        )
        self.assertTrue(
            any(marker["code"] == "missing_lineage_edge" for marker in result["conflict_markers"])
        )

    def test_query_unknown_family(self):
        with self.assertRaises(ValueError):
            self.service.query("unknown_family", binding_id="x")

    def test_query_missing_param(self):
        with self.assertRaises(ValueError):
            self.service.query("runtime_binding_projection")

    def test_query_source_runtime_trace_missing_param(self):
        with self.assertRaises(ValueError):
            self.service.query("source_runtime_telemetry_trace")

    def test_load_real_corpus(self):
        corpus_path = Path(__file__).parent.parent.parent / "registry" / "lineage" / "lin001a_benchmark_corpus.json"
        if corpus_path.exists():
            corpus = json.loads(corpus_path.read_text())
            svc = LineageReadService()
            svc.load_corpus(corpus)
            # Should not raise
            result = svc.query("runtime_binding_projection", binding_id="rb-alpha-live-001")
            self.assertEqual(result["target_id"], "rb-alpha-live-001")


class TestBenchmarkValidation(unittest.TestCase):
    """Validate the service against the LIN-001A benchmark corpus."""

    @classmethod
    def setUpClass(cls):
        corpus_path = Path(__file__).parent.parent.parent / "registry" / "lineage" / "lin001a_benchmark_corpus.json"
        if not corpus_path.exists():
            raise unittest.SkipTest("LIN-001A benchmark corpus not found")
        cls.corpus = json.loads(corpus_path.read_text())
        cls.service = LineageReadService()
        cls.service.load_corpus(cls.corpus)

    def test_all_benchmark_cases_pass(self):
        for case in self.corpus.get("benchmark_cases", []):
            result = self.service.query(case["query_family"], **case["params"])
            # Flatten IDs
            observed = set()
            for item in result.get("upstream_chain", []):
                observed.add(item["id"])
            for item in result.get("downstream_chain", []):
                observed.add(item["id"])
            for item in result.get("conflict_markers", []):
                if "id" in item:
                    observed.add(item["id"])
            # Also check chain items for rollback metadata
            for chain in [result.get("upstream_chain", []), result.get("downstream_chain", [])]:
                for item in chain:
                    if item.get("rollback_parent"):
                        observed.add(item["rollback_parent"])
                    if item.get("rollback_action_type"):
                        observed.add(item["rollback_action_type"])

            missing = [i for i in case.get("expected_ids", []) if i not in observed]
            missing_markers = [i for i in case.get("expected_marker_ids", []) if i not in observed]
            self.assertEqual(missing, [], f"Case {case['case_id']} missing IDs: {missing}")
            self.assertEqual(missing_markers, [], f"Case {case['case_id']} missing markers: {missing_markers}")


class TestSummaryEnvelopeContract(unittest.TestCase):
    """Regression tests for the LIN-001 summary projection envelope contract."""

    REQUIRED_REFS = {
        "strategy_ids", "registry_ids", "runtime_binding_ids",
        "deployment_plan_ids", "capital_pool_ids",
        "persona_capital_binding_ids", "artifact_refs", "trace_ids",
    }

    def setUp(self):
        self.service = LineageReadService()
        self.service.load_corpus(_MINIMAL_CORPUS)

    def test_all_families_have_derived_only(self):
        for family, params in [
            ("runtime_binding_projection", {"binding_id": "rb-1"}),
            ("capital_pool_projection", {"pool_id": "pool-1"}),
            ("telemetry_event_trace", {"event_id": "evt-1"}),
            ("forensic_plan_trace", {"plan_id": "plan-1"}),
        ]:
            result = self.service.query(family, **params)
            self.assertIs(
                result.get("derived_only"), True,
                f"{family}: derived_only must be explicitly True",
            )

    def test_all_families_have_complete_refs(self):
        for family, params in [
            ("runtime_binding_projection", {"binding_id": "rb-1"}),
            ("capital_pool_projection", {"pool_id": "pool-1"}),
            ("telemetry_event_trace", {"event_id": "evt-1"}),
            ("forensic_plan_trace", {"plan_id": "plan-1"}),
        ]:
            result = self.service.query(family, **params)
            self.assertIn("refs", result, f"{family}: missing refs key")
            refs = result["refs"]
            self.assertIsInstance(refs, dict)
            missing_keys = self.REQUIRED_REFS - set(refs.keys())
            self.assertEqual(
                missing_keys, set(),
                f"{family}: refs missing keys: {missing_keys}",
            )

    def test_alias_drift_markers_surfaced_in_telemetry_trace(self):
        """Verify that alias mismatch conflicts from feedback_adapter logic are detected."""
        alias_drift_corpus = {
            "metadata": {"task_id": "LIN-002-ALIAS-TEST"},
            "node_sets": {
                "capital_pools": [
                    {"pool_id": "pool-1", "single_runtime_enforced": True, "created_at": "2026-04-10T00:00:00Z"}
                ],
                "persona_capital_bindings": [
                    {"binding_id": "pb-1", "capital_pool_id": "pool-1", "created_at": "2026-04-10T00:00:00Z"}
                ],
                "deployment_plans": [
                    {
                        "plan_id": "plan-1", "capital_pool_id": "pool-1", "binding_id": "pb-1",
                        "artifact_id": "art-1", "artifact_version": "1.0.0",
                        "created_at": "2026-04-10T00:00:00Z",
                    }
                ],
                "runtime_bindings": [
                    {
                        "binding_id": "rb-1", "capital_pool_id": "pool-1", "plan_id": "plan-1",
                        "persona_capital_binding_id": "pb-1", "artifact_id": "art-1",
                        "artifact_version": "1.0.0", "runtime_id": "rt-1", "status": "active",
                        "effective_at": "2026-04-10T00:00:00Z",
                    }
                ],
                "telemetry_events": [
                    {
                        "event_id": "evt-alias",
                        "event_type": "deploy_completed",
                        "binding_id": "rb-legacy",
                        "runtime_binding_id": "rb-1",
                        "plan_id": "plan-legacy",
                        "deployment_plan_id": "plan-1",
                        "capital_pool_id": "pool-1",
                        "persona_capital_binding_id": "pb-1",
                        "artifact_id": "art-1",
                        "artifact_version": "1.0.1",
                        "target": {"artifact_version": "1.0.0"},
                        "runtime_id": "rt-1",
                        "deployment_stage": "live",
                        "environment": "canary",
                        "event_produced_at": "2026-04-10T00:00:01Z",
                    }
                ],
            },
            "query_families": [],
            "benchmark_cases": [],
        }
        svc = LineageReadService()
        svc.load_corpus(alias_drift_corpus)
        result = svc.query("telemetry_event_trace", event_id="evt-alias")
        codes = {m["code"] for m in result["conflict_markers"]}
        expected = {
            "runtime_binding_alias_mismatch",
            "deployment_plan_alias_mismatch",
            "deployment_stage_alias_mismatch",
            "artifact_version_target_mismatch",
        }
        self.assertEqual(
            expected - codes, set(),
            f"Expected alias drift markers not found. Got: {codes}",
        )

    def test_telemetry_event_trace_target_carried_refs_appear_in_values(self):
        """Verify that trace_id/strategy_id/registry_id from the target event
        itself (not just chain items) appear in refs values — not merely keys."""
        target_refs_corpus = {
            "metadata": {"task_id": "LIN-002-TARGET-REFS"},
            "node_sets": {
                "capital_pools": [
                    {"pool_id": "pool-1", "single_runtime_enforced": True,
                     "created_at": "2026-04-10T00:00:00Z"}
                ],
                "persona_capital_bindings": [
                    {"binding_id": "pb-1", "capital_pool_id": "pool-1",
                     "created_at": "2026-04-10T00:00:00Z"}
                ],
                "deployment_plans": [
                    {
                        "plan_id": "plan-1", "capital_pool_id": "pool-1",
                        "binding_id": "pb-1", "artifact_id": "art-1",
                        "artifact_version": "1.0.0",
                        "created_at": "2026-04-10T00:00:00Z",
                    }
                ],
                "runtime_bindings": [
                    {
                        "binding_id": "rb-1", "capital_pool_id": "pool-1",
                        "plan_id": "plan-1", "persona_capital_binding_id": "pb-1",
                        "artifact_id": "art-1", "artifact_version": "1.0.0",
                        "runtime_id": "rt-1", "status": "active",
                        "effective_at": "2026-04-10T00:00:00Z",
                    }
                ],
                "telemetry_events": [
                    {
                        "event_id": "evt-target-refs",
                        "event_type": "deploy_completed",
                        "binding_id": "rb-1",
                        "plan_id": "plan-1",
                        "capital_pool_id": "pool-1",
                        "persona_capital_binding_id": "pb-1",
                        "artifact_id": "art-1",
                        "artifact_version": "1.0.0",
                        "runtime_id": "rt-1",
                        "trace_id": "trace-xyz",
                        "strategy_id": "strat-abc",
                        "registry_id": "reg-def",
                        "event_produced_at": "2026-04-10T00:00:01Z",
                    }
                ],
            },
            "query_families": [],
            "benchmark_cases": [],
        }
        svc = LineageReadService()
        svc.load_corpus(target_refs_corpus)
        result = svc.query("telemetry_event_trace", event_id="evt-target-refs")
        refs = result["refs"]
        self.assertIn("trace-xyz", refs["trace_ids"],
                      f"trace_ids should contain target event's trace_id, got {refs['trace_ids']}")
        self.assertIn("strat-abc", refs["strategy_ids"],
                      f"strategy_ids should contain target event's strategy_id, got {refs['strategy_ids']}")
        self.assertIn("reg-def", refs["registry_ids"],
                      f"registry_ids should contain target event's registry_id, got {refs['registry_ids']}")


if __name__ == "__main__":
    unittest.main()
