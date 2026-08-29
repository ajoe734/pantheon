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

from fastapi.testclient import TestClient

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

    def test_main_py_all_read_store_attributes_are_inventoried_and_mapped(self) -> None:
        """Prove that all 202 read_store attributes in main.py are inventoried and mapped or isolated."""
        main_py = BFF_DIR / "main.py"
        self.assertTrue(main_py.exists(), f"main.py not found at {main_py}")
        tree = ast.parse(main_py.read_text(encoding="utf-8"), filename=str(main_py))

        read_store_attrs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "read_store":
                    read_store_attrs.add(node.attr)

        self.assertGreater(len(read_store_attrs), 100, "Expected >100 read_store attributes in main.py")

        ports_instance = create_read_surface_ports()

        unmapped: list[str] = []
        mapped_all: list[str] = []

        for attr in sorted(read_store_attrs):
            if hasattr(ports_instance, attr):
                mapped_all.append(attr)
            else:
                unmapped.append(attr)

        self.assertEqual(
            unmapped,
            [],
            f"Found unmapped read_store attributes in main.py without domain port mapping: {unmapped}",
        )
        self.assertEqual(len(mapped_all), len(read_store_attrs))
        self.assertGreaterEqual(len(mapped_all), 200)


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


class TestReadSurfacePortsRetainedCallerContracts(unittest.TestCase):
    """Verifies that ReadSurfacePorts correctly fulfills contracts for retained callers."""

    def setUp(self) -> None:
        self.ports = create_in_memory_read_surface_ports(
            operations_consultation_kwargs={
                "consult_rules": [{"rule_id": "cr-100", "persona_id": "p-100", "min_participants": 3}],
                "consult_memos": [
                    {
                        "memo_id": "memo-100",
                        "linked_session_id": "sess-100",
                        "status": "published",
                        "summary": "Committee Memo 100",
                    },
                    {
                        "memo_id": "memo-200",
                        "linked_session_id": "sess-200",
                        "status": "draft",
                        "summary": "Committee Memo 200",
                    },
                ],
            },
            persona_capital_runtime_kwargs={
                "personas": [{"persona_id": "p-100", "name": "Alpha", "lifecycle_state": "active"}],
                "deployment_plans": [
                    {
                        "plan_id": "dp-100",
                        "id": "dp-100",
                        "status": "proposed",
                        "target_stage": "paper",
                        "approval_decision_id": "app-100",
                    }
                ],
                "capital_pools": [{"pool_id": "cp-100", "name": "Main Pool"}],
                "bindings": [{"binding_id": "b-100", "persona_id": "p-100", "pool_id": "cp-100"}],
                "runtime_bindings": [{"runtime_id": "rt-100", "binding_id": "b-100", "status": "running"}],
            },
            ooda_management_kwargs={
                "approval_decisions": [
                    {
                        "decision_id": "app-100",
                        "id": "app-100",
                        "state": "pending",
                        "outcome": "pending",
                        "reviewer": "RiskCommittee",
                        "risk_level": "low",
                        "decided_at": "2026-08-28T00:00:00Z",
                    }
                ],
            },
            lifecycle_telemetry_governance_kwargs={
                "paper_live_drift_reports": [
                    {
                        "session_id": "sess-drift-1",
                        "id": "sess-drift-1",
                        "runtime_id": "rt-100",
                        "binding_id": "b-100",
                        "active": True,
                    }
                ],
            },
        )

    def test_get_committee_session_memo_positional_and_keyword(self) -> None:
        # Two positional arguments: (session_id, memo_id)
        memo = self.ports.get_committee_session_memo("sess-100", "memo-100")
        self.assertIsNotNone(memo)
        self.assertEqual(memo["memo_id"], "memo-100")

        # Single positional argument: (memo_id)
        memo_single = self.ports.get_committee_session_memo("memo-100")
        self.assertIsNotNone(memo_single)
        self.assertEqual(memo_single["memo_id"], "memo-100")

        # Keyword arguments: session_id=..., memo_id=...
        memo_kw = self.ports.get_committee_session_memo(session_id="sess-100", memo_id="memo-100")
        self.assertIsNotNone(memo_kw)
        self.assertEqual(memo_kw["memo_id"], "memo-100")

        # Session mismatch must return None
        memo_mismatch = self.ports.get_committee_session_memo("sess-wrong", "memo-100")
        self.assertIsNone(memo_mismatch)

        # Non-existent memo must return None
        memo_none = self.ports.get_committee_session_memo("sess-100", "memo-missing")
        self.assertIsNone(memo_none)

    def test_list_committee_session_memos_filtered_and_unfiltered(self) -> None:
        # Filtered by positional session_id
        memos = self.ports.list_committee_session_memos("sess-100")
        self.assertEqual(len(memos), 1)
        self.assertEqual(memos[0]["memo_id"], "memo-100")

        # Filtered by keyword session_id
        memos_kw = self.ports.list_committee_session_memos(session_id="sess-200")
        self.assertEqual(len(memos_kw), 1)
        self.assertEqual(memos_kw[0]["memo_id"], "memo-200")

        # Unfiltered returns all memos
        all_memos = self.ports.list_committee_session_memos()
        self.assertEqual(len(all_memos), 2)

    def test_get_allowed_actions_positional_and_keyword(self) -> None:
        # Positional plan_id
        actions = self.ports.get_allowed_actions("dp-100")
        self.assertIsInstance(actions, dict)
        self.assertTrue(actions.get("canApprove"))
        self.assertTrue(actions.get("canReject"))

        # Keyword plan_id
        actions_kw = self.ports.get_allowed_actions(plan_id="dp-100")
        self.assertIsInstance(actions_kw, dict)
        self.assertTrue(actions_kw.get("canApprove"))

        # Empty / non-existent plan
        actions_empty = self.ports.get_allowed_actions("dp-nonexistent")
        self.assertFalse(actions_empty.get("canApprove"))
        self.assertFalse(actions_empty.get("canReject"))
        self.assertFalse(actions_empty.get("canPromoteToPaper"))

    def test_get_latest_run_positional_and_keyword(self) -> None:
        # Positional plan_id
        run = self.ports.get_latest_run("dp-100")
        self.assertIsInstance(run, dict)

        # Keyword plan_id
        run_kw = self.ports.get_latest_run(plan_id="dp-100")
        self.assertIsInstance(run_kw, dict)

        # No arguments
        run_none = self.ports.get_latest_run()
        self.assertTrue(run_none is None or isinstance(run_none, dict))

    def test_get_review_summary_positional_and_keyword(self) -> None:
        # Positional plan_id
        review = self.ports.get_review_summary("dp-100")
        self.assertIsNotNone(review)
        self.assertEqual(review.get("governanceOutcome"), "pending")
        self.assertEqual(review.get("reviewer"), "RiskCommittee")

        # Keyword plan_id
        review_kw = self.ports.get_review_summary(plan_id="dp-100")
        self.assertIsNotNone(review_kw)
        self.assertEqual(review_kw.get("governanceOutcome"), "pending")

        # No arguments falls back to diagnostic surface status
        review_diag = self.ports.get_review_summary()
        self.assertIsInstance(review_diag, dict)

    def test_get_consult_policy_positional_and_keyword(self) -> None:
        # Positional persona_id
        policy = self.ports.get_consult_policy("p-100")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.get("rule_id"), "cr-100")

        # Keyword persona_id
        policy_kw = self.ports.get_consult_policy(persona_id="p-100")
        self.assertIsNotNone(policy_kw)
        self.assertEqual(policy_kw.get("rule_id"), "cr-100")

        # Non-existent persona returns None
        policy_none = self.ports.get_consult_policy("p-nonexistent")
        self.assertIsNone(policy_none)

        # get_persona_consult_policy alias
        policy_alias = self.ports.get_persona_consult_policy("p-100")
        self.assertEqual(policy_alias, policy)

    def test_get_paper_runtime_monitoring_session_positional_and_keyword(self) -> None:
        # Positional session_id
        mon = self.ports.get_paper_runtime_monitoring_session("sess-drift-1")
        self.assertIsNotNone(mon)
        self.assertEqual(mon.get("runtime_id"), "rt-100")

        # Keyword runtime_id & binding_id
        mon_kw = self.ports.get_paper_runtime_monitoring_session(runtime_id="rt-100", binding_id="b-100")
        self.assertIsNotNone(mon_kw)
        self.assertEqual(mon_kw.get("session_id"), "sess-drift-1")

        # get_paper_live_drift_report
        drift = self.ports.get_paper_live_drift_report("rt-100")
        self.assertIsNotNone(drift)
        self.assertEqual(drift.get("runtime_id"), "rt-100")

    def test_get_persona_allowed_actions(self) -> None:
        actions = self.ports.get_persona_allowed_actions("p-100")
        self.assertIsNotNone(actions)
        self.assertTrue(actions.get("canEdit"))
        self.assertTrue(actions.get("canRetire"))


class TestEndpointLevelRetainedCallers(unittest.TestCase):
    """Endpoint-level regressions proving main.py retained callers execute cleanly through ReadSurfacePorts."""

    def setUp(self) -> None:
        import main as bff_main

        self.original_read_store = bff_main.read_store
        self.ports = create_in_memory_read_surface_ports(
            operations_consultation_kwargs={
                "consult_requests": [
                    {
                        "request_id": "sess-comm-1",
                        "status": "active",
                        "mode": "committee",
                        "from_persona_id": "p-100",
                        "task": "Committee evaluation",
                    }
                ],
                "consult_rules": [{"rule_id": "cr-100", "persona_id": "p-100", "min_participants": 3}],
                "consult_memos": [
                    {
                        "memo_id": "memo-comm-1",
                        "linked_session_id": "sess-comm-1",
                        "status": "published",
                        "summary": "Evaluation passed",
                    }
                ],
            },
            persona_capital_runtime_kwargs={
                "personas": [{"persona_id": "p-100", "name": "Alpha", "lifecycle_state": "active"}],
                "deployment_plans": [
                    {
                        "plan_id": "dp-comm-1",
                        "id": "dp-comm-1",
                        "status": "proposed",
                        "target_stage": "paper",
                        "stage": "paper",
                        "artifact_id": "art-100",
                        "approval_decision_id": "app-comm-1",
                        "capital_pool_id": "cp-100",
                        "runtime_binding_id": "rb-100",
                    }
                ],
                "capital_pools": [{"pool_id": "cp-100", "id": "cp-100", "name": "Main Pool"}],
                "bindings": [{"binding_id": "b-100", "persona_id": "p-100", "pool_id": "cp-100"}],
                "runtime_bindings": [
                    {
                        "runtime_id": "rt-100",
                        "id": "rb-100",
                        "runtime_binding_id": "rb-100",
                        "binding_id": "b-100",
                        "status": "running",
                    }
                ],
            },
            ooda_management_kwargs={
                "approval_decisions": [
                    {
                        "decision_id": "app-comm-1",
                        "id": "app-comm-1",
                        "state": "pending",
                        "outcome": "pending",
                        "reviewer": "RiskLead",
                        "risk_level": "low",
                        "decided_at": "2026-08-28T00:00:00Z",
                    }
                ],
            },
            lifecycle_telemetry_governance_kwargs={
                "paper_live_drift_reports": [
                    {
                        "session_id": "sess-drift-1",
                        "id": "sess-drift-1",
                        "runtime_id": "rt-100",
                        "binding_id": "b-100",
                        "active": True,
                    }
                ],
            },
        )
        bff_main.read_store = self.ports
        self.client = TestClient(bff_main.app, raise_server_exceptions=False)
        self.auth_headers = {"Authorization": "Bearer admin:admin"}

    def tearDown(self) -> None:
        import main as bff_main
        bff_main.read_store = self.original_read_store

    def test_endpoint_list_committee_session_memos(self) -> None:
        response = self.client.get(
            "/bff/agora/committee/sessions/sess-comm-1/memos",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["memo_id"], "memo-comm-1")

    def test_endpoint_get_committee_session_memo_detail(self) -> None:
        response = self.client.get(
            "/bff/agora/committee/sessions/sess-comm-1/memos/memo-comm-1",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("data", data)
        self.assertEqual(data["data"]["memo_id"], "memo-comm-1")

    def test_endpoint_deployment_plans_list(self) -> None:
        response = self.client.get(
            "/api/v1/operator/deployment-plans",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("items", data)
        self.assertGreaterEqual(len(data["items"]), 1)
        plan_item = data["items"][0]
        self.assertEqual(plan_item["plan_id"], "dp-comm-1")
        self.assertEqual(plan_item["governance_outcome"], "pending")

    def test_endpoint_deployment_review_detail(self) -> None:
        response = self.client.get(
            "/api/v1/operator/deployment-review/dp-comm-1",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        res = response.json()
        self.assertIn("data", res)
        data = res["data"]
        self.assertIn("allowedActions", data)
        self.assertIn("latestRun", data)
        self.assertIn("review", data)
        self.assertTrue(data["allowedActions"]["canApprove"])

    def test_endpoint_persona_consult_policy(self) -> None:
        response = self.client.get(
            "/api/v1/personas/p-100/consult-policy",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("data", data)
        self.assertEqual(data["data"].get("rule_id"), "cr-100")

    def test_endpoint_create_agora_signal(self) -> None:
        import os
        old_mode = os.environ.get("PANTHEON_BFF_AUTH_MODE")
        old_stub = os.environ.get("PANTHEON_BFF_AUTH_STUB")
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        try:
            response = self.client.post(
                "/bff/agora/signals",
                json={
                    "title": "Regime Change Signal",
                    "body": "Volatility spike detected across core asset classes",
                    "source": "manual",
                    "confidence": 0.95,
                    "severity": "info",
                },
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "sig-test-idem-1"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            data = response.json().get("data", {})
            self.assertTrue(data.get("signalId") or data.get("id"))
            self.assertEqual(data.get("title"), "Regime Change Signal")
        finally:
            if old_mode is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = old_mode
            if old_stub is None:
                os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = old_stub

    def test_endpoint_create_agora_note(self) -> None:
        import os
        old_mode = os.environ.get("PANTHEON_BFF_AUTH_MODE")
        old_stub = os.environ.get("PANTHEON_BFF_AUTH_STUB")
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        try:
            response = self.client.post(
                "/bff/agora/notes",
                json={
                    "title": "Analysis Note",
                    "body": "Observed market conditions for Q3",
                    "attachment_type": "free_standing",
                },
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "note-test-idem-1"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            data = response.json().get("data", {})
            self.assertTrue(data.get("note_id") or data.get("id"))
            self.assertEqual(data.get("title"), "Analysis Note")
        finally:
            if old_mode is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = old_mode
            if old_stub is None:
                os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = old_stub

    def test_endpoint_create_and_patch_agora_journal(self) -> None:
        import json
        import os
        old_mode = os.environ.get("PANTHEON_BFF_AUTH_MODE")
        old_stub = os.environ.get("PANTHEON_BFF_AUTH_STUB")
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        try:
            # Create
            response = self.client.post(
                "/bff/agora/journal",
                json={
                    "title": "Strategy Journal Initial",
                    "body": "Initial trade rationale",
                    "tags": ["strategy"],
                    "linked_strategy_ids": [],
                    "linked_persona_ids": [],
                    "visibility": "public",
                },
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "jrn-test-idem-1"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            entry = response.json().get("data", {})
            entry_id = entry.get("entry_id") or entry.get("id")
            self.assertIsNotNone(entry_id)

            # Patch
            patch_resp = self.client.patch(
                f"/bff/agora/journal/{entry_id}",
                content=json.dumps({"title": "Strategy Journal Updated"}),
                headers={
                    "Authorization": "Bearer admin:admin,trader",
                    "Idempotency-Key": "jrn-test-idem-2",
                    "Content-Type": "application/merge-patch+json",
                },
            )
            self.assertEqual(patch_resp.status_code, 200, patch_resp.text)
            patched_entry = patch_resp.json().get("data", {})
            self.assertEqual(patched_entry.get("title"), "Strategy Journal Updated")
        finally:
            if old_mode is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = old_mode
            if old_stub is None:
                os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = old_stub

    def test_endpoint_agora_committee_session_flow(self) -> None:
        import os
        old_mode = os.environ.get("PANTHEON_BFF_AUTH_MODE")
        old_stub = os.environ.get("PANTHEON_BFF_AUTH_STUB")
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        try:
            # 1. Create committee session
            create_resp = self.client.post(
                "/bff/agora/committee/sessions",
                json={"title": "Deployment Evaluation", "participants": ["p-100"]},
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "comm-test-idem-1"},
            )
            self.assertEqual(create_resp.status_code, 201, create_resp.text)
            sess = create_resp.json().get("data", {})
            sess_id = sess.get("sessionId") or sess.get("id")
            self.assertIsNotNone(sess_id)

            # 2. Open session
            open_resp = self.client.post(
                f"/bff/agora/committee/sessions/{sess_id}/open",
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "comm-test-idem-2"},
            )
            self.assertEqual(open_resp.status_code, 200, open_resp.text)

            # 3. Submit memo
            memo_resp = self.client.post(
                f"/bff/agora/committee/sessions/{sess_id}/memos",
                json={"summary": "Approved deployment plan", "recommendations": ["deploy"]},
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "comm-test-idem-3"},
            )
            self.assertEqual(memo_resp.status_code, 201, memo_resp.text)
            memo_id = memo_resp.json().get("data", {}).get("memo_id")
            self.assertIsNotNone(memo_id)

            # 4. Publish memo
            pub_resp = self.client.post(
                f"/bff/agora/committee/sessions/{sess_id}/memos/{memo_id}/publish",
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "comm-test-idem-4"},
            )
            self.assertEqual(pub_resp.status_code, 200, pub_resp.text)
            self.assertEqual(pub_resp.json().get("data", {}).get("status"), "published")

            # 5. Close session
            close_resp = self.client.post(
                f"/bff/agora/committee/sessions/{sess_id}/close",
                json={"outcome": "approved", "memoIds": [memo_id]},
                headers={"Authorization": "Bearer admin:admin,trader", "Idempotency-Key": "comm-test-idem-5"},
            )
            self.assertEqual(close_resp.status_code, 200, close_resp.text)
        finally:
            if old_mode is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = old_mode
            if old_stub is None:
                os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = old_stub

    def test_endpoint_research_ticket_lifecycle(self) -> None:
        import os
        old_mode = os.environ.get("PANTHEON_BFF_AUTH_MODE")
        old_stub = os.environ.get("PANTHEON_BFF_AUTH_STUB")
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        try:
            # Create ticket
            resp = self.client.post(
                "/api/v1/research/tickets",
                json={"title": "Test Drift Ticket", "description": "Investigation into drift", "owner": "admin", "priority": "high"},
                headers={"Authorization": "Bearer admin:admin,trader"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            ticket_id = resp.json().get("ticket_id")
            self.assertIsNotNone(ticket_id)

            # Get ticket
            resp_get = self.client.get(f"/api/v1/research/tickets/{ticket_id}", headers={"Authorization": "Bearer admin:admin,trader"})
            self.assertEqual(resp_get.status_code, 200, resp_get.text)
            self.assertEqual(resp_get.json().get("title"), "Test Drift Ticket")

            # Patch ticket
            resp_patch = self.client.patch(
                f"/api/v1/research/tickets/{ticket_id}",
                json={"title": "Updated Drift Ticket Title", "status": "in_progress"},
                headers={"Authorization": "Bearer admin:admin,trader"},
            )
            self.assertEqual(resp_patch.status_code, 200, resp_patch.text)
            self.assertEqual(resp_patch.json().get("status"), "in_progress")
        finally:
            if old_mode is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = old_mode
            if old_stub is None:
                os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = old_stub
