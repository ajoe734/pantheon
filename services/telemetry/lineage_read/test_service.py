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
    NODE_CAPITAL_POOL,
    NODE_PERSONA_BINDING,
    NODE_DEPLOYMENT_PLAN,
    NODE_RUNTIME_BINDING,
    NODE_TELEMETRY_EVENT,
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

    def test_query_unknown_family(self):
        with self.assertRaises(ValueError):
            self.service.query("unknown_family", binding_id="x")

    def test_query_missing_param(self):
        with self.assertRaises(ValueError):
            self.service.query("runtime_binding_projection")

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
