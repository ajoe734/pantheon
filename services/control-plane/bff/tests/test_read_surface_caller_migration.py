"""Tests for ReadSurfaceStore caller migration in main.py and persona_client.py.

Validates acceptance criteria for ACG-RS-CALLER-MIGRATION-20260828:
1. main.py contains no ReadSurfaceStore import, constructor, or direct method call
2. persona_client.py contains no ReadSurfaceStore import, constructor, or direct method call
3. Caller-migration tests prove mapped ports preserve read behavior across all domains
4. BFF startup and targeted tests pass with ReadSurfacePorts
5. No modification to read_store.py in this task
6. Static regression rejecting new mutation APIs or local overlay persistence on ReadSurfacePorts
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import MagicMock, patch

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from ports import (
    ReadSurfacePorts,
    create_read_surface_ports,
    create_in_memory_read_surface_ports,
)
from agora.interaction.persona_client import (
    PersonaReadPort,
    build_canonical_persona_client,
)


class TestAstDecouplingGuards(unittest.TestCase):
    """Verifies that main.py and persona_client.py contain zero ReadSurfaceStore references."""

    def test_main_py_has_no_read_surface_store_references(self) -> None:
        main_py = BFF_DIR / "main.py"
        self.assertTrue(main_py.exists(), f"main.py not found at {main_py}")
        content = main_py.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(main_py))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "read_store":
                    for alias in node.names:
                        self.assertNotEqual(
                            alias.name,
                            "ReadSurfaceStore",
                            f"Forbidden import ReadSurfaceStore in main.py:{node.lineno}",
                        )
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "ReadSurfaceStore":
                    self.fail(f"Forbidden ReadSurfaceStore constructor call in main.py:{node.lineno}")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "ReadSurfaceStore":
                    self.fail(f"Forbidden ReadSurfaceStore attribute call in main.py:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id == "ReadSurfaceStore":
                self.fail(f"Forbidden ReadSurfaceStore identifier in main.py:{node.lineno}")

    def test_persona_client_py_has_no_read_surface_store_references(self) -> None:
        client_py = BFF_DIR / "agora" / "interaction" / "persona_client.py"
        self.assertTrue(client_py.exists(), f"persona_client.py not found at {client_py}")
        content = client_py.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(client_py))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "read_store":
                    for alias in node.names:
                        self.assertNotEqual(
                            alias.name,
                            "ReadSurfaceStore",
                            f"Forbidden import ReadSurfaceStore in persona_client.py:{node.lineno}",
                        )
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "ReadSurfaceStore":
                    self.fail(f"Forbidden ReadSurfaceStore constructor call in persona_client.py:{node.lineno}")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "ReadSurfaceStore":
                    self.fail(f"Forbidden ReadSurfaceStore attribute call in persona_client.py:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id == "ReadSurfaceStore":
                self.fail(f"Forbidden ReadSurfaceStore identifier in persona_client.py:{node.lineno}")

    def test_ports_package_has_no_read_surface_store_instantiations(self) -> None:
        ports_dir = BFF_DIR / "ports"
        self.assertTrue(ports_dir.exists(), f"ports dir not found at {ports_dir}")

        for py_file in ports_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "ReadSurfaceStore":
                        self.fail(f"Forbidden ReadSurfaceStore instantiation in {py_file.name}:{node.lineno}")
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "ReadSurfaceStore":
                        self.fail(f"Forbidden ReadSurfaceStore attribute call in {py_file.name}:{node.lineno}")


class TestStaticRegressionReadSurfacePorts(unittest.TestCase):
    """Static regression tests verifying ReadSurfacePorts remains narrow, read-only, and without overlay state."""

    def test_read_surface_ports_has_no_mutation_methods(self) -> None:
        """Reject new mutation APIs on ReadSurfacePorts (e.g. create_*, update_*, put_*, record_*, etc.)."""
        forbidden_mutation_prefixes = (
            "create_persona",
            "update_persona",
            "create_deployment_plan",
            "create_runtime_binding",
            "put_allocation_evaluation",
            "put_ranking_snapshot",
            "record_agora_audit_event",
            "record_agora_signal_feedback",
            "record_sponsor_decision",
            "patch_capital_pool",
            "patch_decision_journal_entry",
            "patch_ranking_formula",
            "patch_research_ticket",
            "submit_committee_session_memo",
            "publish_committee_session_memo",
            "open_committee_session",
            "close_committee_session",
        )
        for attr in dir(ReadSurfacePorts):
            for prefix in forbidden_mutation_prefixes:
                self.assertFalse(
                    attr.startswith(prefix),
                    f"ReadSurfacePorts must remain read-only; found forbidden mutation API '{attr}'",
                )

    def test_read_surface_ports_has_no_local_overlay_persistence(self) -> None:
        """Verify that ReadSurfacePorts instances do not hold local overlay storage dicts."""
        instance = create_read_surface_ports()
        forbidden_attrs = ("_data", "_storage", "_overlay", "_local_store", "_local_data")
        for attr in forbidden_attrs:
            self.assertFalse(
                hasattr(instance, attr),
                f"ReadSurfacePorts must not recreate local overlay persistence ({attr})",
            )


class TestAgoraPersonaClientMigration(unittest.TestCase):
    """Verifies that Agora persona_client.build_canonical_persona_client satisfies PersonaReadPort."""

    def test_build_canonical_persona_client_satisfies_protocol(self) -> None:
        client = build_canonical_persona_client()
        self.assertTrue(
            isinstance(client, PersonaReadPort),
            f"Client {type(client)} does not satisfy PersonaReadPort protocol",
        )

    def test_persona_client_list_personas_and_capability_snapshot(self) -> None:
        client = build_canonical_persona_client()
        self.assertTrue(callable(client.list_personas))
        self.assertTrue(callable(client.get_capability_snapshot))

        personas = client.list_personas()
        self.assertIsInstance(personas, list)

        snapshot = client.get_capability_snapshot(None)
        self.assertIsNone(snapshot)


class TestReadSurfacePortsPreservesBehavior(unittest.TestCase):
    """Verifies that ReadSurfacePorts mapped ports preserve read behavior across all 6 domain areas."""

    def setUp(self) -> None:
        self.ports = create_in_memory_read_surface_ports(
            operations_consultation_kwargs={
                "workflow_templates": [{"id": "wf-100", "name": "Deploy Guard"}],
                "hook_registry": [{"id": "hk-100", "name": "Safety Check"}],
                "governance_permissions": [{"role": "admin", "action": "override"}],
                "memory_governance_rules": [{"rule_id": "mg-100", "retention_days": 30}],
                "consult_rules": [{"rule_id": "cr-100", "min_participants": 3}],
                "route_policies": [{"route": "/bff/v1", "rate_limit": 50}],
            },
            persona_capital_runtime_kwargs={
                "personas": [{"persona_id": "p-100", "name": "Alpha", "lifecycle_state": "active"}],
                "capital_pools": [{"pool_id": "cp-100", "name": "Main Pool", "total_allocated": 1000000}],
                "bindings": [{"binding_id": "b-100", "persona_id": "p-100", "pool_id": "cp-100"}],
                "deployment_plans": [{"plan_id": "dp-100", "target": "paper", "status": "proposed"}],
                "runtime_bindings": [{"runtime_id": "rt-100", "binding_id": "b-100", "status": "running"}],
                "rankings": [{"ranking_id": "rk-100", "score": 98.5}],
                "ranking_formulas": [{"formula_id": "rf-100", "expression": "sharpe * 0.7"}],
                "persona_league": [{"persona_id": "p-100", "tier": "gold"}],
                "rebalances": [{"rebalance_id": "reb-100", "status": "executed"}],
                "capital_allocations": [{"allocation_id": "ca-100", "amount": 500000}],
                "containments": [{"containment_id": "ct-100", "persona_id": "p-100", "status": "contained"}],
                "evolution_programs": [{"program_id": "ev-100", "status": "active"}],
                "evolution_decisions": [{"decision_id": "ed-100", "verdict": "promote"}],
            },
            ooda_management_kwargs={
                "ooda_packets": [{"id": "pkt-100", "strategy_id": "strat-100", "runtime_id": "rt-100"}],
                "interventions": [{"id": "int-100", "action": "pause"}],
                "synthesis_conflict_logs": [{"id": "log-100", "conflict_type": "divergence"}],
                "approval_decisions": [{"decision_id": "app-100", "state": "under_review", "target_type": "ApprovalDecision"}],
                "deployment_diffs": {"dp-100": {"plan_id": "dp-100", "diff": "allocated +50k"}},
            },
            research_knowledge_source_kwargs={
                "research_notes_store": {"note-100": {"id": "note-100", "title": "Market Regime Shift"}},
                "evidence_refs_store": {"ev-ref-100": {"id": "ev-ref-100", "source": "sec_10k"}},
                "insight_cards_store": {"card-100": {"id": "card-100", "summary": "Momentum breakdown"}},
                "strategy_specs_store": {"spec-100": {"id": "spec-100", "version": "v1.2"}},
                "institutional_memory_store": None,
                "data_source_registry": None,
            },
            lifecycle_telemetry_governance_kwargs={
                "incidents": {"inc-100": {"id": "inc-100", "severity": "P1", "status": "resolved"}},
                "postmortems": {"pm-100": {"id": "pm-100", "incident_id": "inc-100"}},
                "kill_switch": {"enabled": False, "status": "armed"},
                "governance_audit_events": [{"id": "gov-100", "action": "freeze"}],
                "lineage_edges": [{"source": "order-1", "target": "fill-1"}],
                "telemetry_events": [{"id": "tel-100", "latency_ms": 12}],
            },
            persona_training_kwargs={},
        )

    def test_operations_consultation_delegates(self) -> None:
        self.assertEqual(len(self.ports.list_workflow_templates()), 1)
        self.assertEqual(self.ports.list_workflow_templates()[0]["id"], "wf-100")
        self.assertEqual(len(self.ports.list_hook_registry()), 1)
        self.assertEqual(self.ports.list_hook_registry()[0]["id"], "hk-100")
        self.assertEqual(len(self.ports.list_governance_permissions()), 1)
        self.assertEqual(len(self.ports.list_memory_governance_rules()), 1)
        self.assertEqual(len(self.ports.list_consult_rules()), 1)
        self.assertEqual(len(self.ports.list_route_policies()), 1)

    def test_persona_capital_runtime_delegates(self) -> None:
        personas = self.ports.list_personas()
        self.assertEqual(len(personas), 1)
        self.assertEqual(personas[0]["persona_id"], "p-100")

        persona = self.ports.get_persona("p-100")
        self.assertIsNotNone(persona)
        self.assertEqual(persona["persona_id"], "p-100")

        self.assertEqual(len(self.ports.list_capital_pools()), 1)
        self.assertEqual(len(self.ports.list_bindings()), 1)
        self.assertEqual(len(self.ports.list_deployment_plans()), 1)
        self.assertEqual(len(self.ports.list_runtime_bindings()), 1)
        self.assertEqual(len(self.ports.list_rankings()), 1)
        self.assertEqual(len(self.ports.list_ranking_formulas()), 1)
        self.assertEqual(len(self.ports.list_persona_league()), 1)
        self.assertEqual(len(self.ports.list_rebalances()), 1)
        self.assertEqual(len(self.ports.list_capital_allocations()), 1)
        self.assertEqual(len(self.ports.list_containments()), 1)
        self.assertEqual(len(self.ports.list_evolution_programs()), 1)
        self.assertEqual(len(self.ports.list_evolution_decisions()), 1)

    def test_ooda_management_delegates(self) -> None:
        self.assertEqual(len(self.ports.list_ooda_packets()), 1)
        self.assertEqual(len(self.ports.list_interventions()), 1)
        self.assertEqual(len(self.ports.list_synthesis_conflict_logs()), 1)
        self.assertEqual(len(self.ports.list_governance_review_queue_items()), 1)
        self.assertEqual(len(self.ports.list_approval_queue_items()), 1)
        diff = self.ports.get_deployment_diff("dp-100")
        self.assertIsNotNone(diff)
        self.assertEqual(diff.get("plan_id"), "dp-100")

    def test_research_knowledge_source_delegates(self) -> None:
        self.assertEqual(len(self.ports.list_research_notes()), 1)
        self.assertEqual(len(self.ports.list_evidence_refs()), 1)
        self.assertEqual(len(self.ports.list_insight_cards()), 1)
        self.assertEqual(len(self.ports.list_strategy_specs()), 1)

    def test_lifecycle_telemetry_governance_delegates(self) -> None:
        self.assertEqual(len(self.ports.list_incidents()), 1)
        self.assertEqual(self.ports.get_incident("inc-100")["severity"], "P1")
        self.assertEqual(len(self.ports.list_postmortems()), 1)
        self.assertEqual(self.ports.get_kill_switch_status()["status"], "armed")
        self.assertEqual(len(self.ports.list_governance_audit_events()), 1)
        self.assertEqual(len(self.ports.list_lineage_edges()), 1)
        self.assertEqual(len(self.ports.list_telemetry_events()), 1)

    def test_trade_journey_projection_reader_override(self) -> None:
        sentinel_reader = MagicMock()
        self.ports._trade_journey_projection_reader_override = sentinel_reader
        self.assertIs(self.ports.trade_journey_projection_reader(), sentinel_reader)

    def test_dynamic_getattr_delegation(self) -> None:
        self.assertTrue(hasattr(self.ports, "list_personas"))
        self.assertTrue(hasattr(self.ports, "list_incidents"))
        self.assertTrue(hasattr(self.ports, "dataset_source"))

        with self.assertRaises(AttributeError):
            _ = self.ports.non_existent_method_xyz_12345()


class TestBffMainDecoupledStartup(unittest.TestCase):
    """Verifies that BFF main module imports and initializes cleanly with ReadSurfacePorts."""

    def test_main_module_read_store_is_read_surface_ports(self) -> None:
        import main as bff_main

        self.assertIsInstance(
            bff_main.read_store,
            ReadSurfacePorts,
            f"Expected bff_main.read_store to be ReadSurfacePorts, got {type(bff_main.read_store)}",
        )

    def test_main_module_app_initialization(self) -> None:
        import main as bff_main

        self.assertIsNotNone(bff_main.app)
        self.assertEqual(bff_main.app.title, "Pantheon Operator BFF")
