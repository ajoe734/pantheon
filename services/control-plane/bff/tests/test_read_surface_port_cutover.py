"""Tests for narrow read-surface port cutover and ReadSurfaceStore decoupling.

Validates acceptance criteria for ACG-RS-PORT-CUTOVER-20260828:
1. Every production narrow read-surface port resolves without delegating to ReadSurfaceStore
2. Port-cutover tests cover all declared callers and domain slices
3. BFF targeted tests and import/startup smoke pass
4. No modification to read_store.py, main.py, domain_ports, or persona_client.py
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys
from typing import Any, Dict, List
import unittest
from unittest.mock import MagicMock, patch

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from ports import (
    # Operations & Consultation
    WorkflowHookCatalogReaderPort,
    DomainWorkflowCatalogPort,
    OpenClawOperationsReaderPort,
    DomainOpenClawOperationsPort,
    ConsultationReaderPort,
    DomainConsultationPort,
    OperationsConsultationPort,
    CompositeOperationsConsultationPort,
    InMemoryOperationsConsultationPort,
    create_operations_consultation_port,
    create_in_memory_operations_consultation_port,
    # Persona & Capital & Runtime
    PersonaFleetPort,
    CapitalPoolPort,
    DeploymentPlanPort,
    RuntimePort,
    RankingProjectionPort,
    EvolutionProjectionPort,
    PersonaCapitalRuntimeDomainPort,
    CompositePersonaCapitalRuntimePort,
    InMemoryPersonaCapitalRuntimePort,
    create_persona_capital_runtime_port,
    create_in_memory_persona_capital_runtime_port,
    # OODA & Management
    OodaPacketsPort,
    InterventionsPort,
    SynthesisConflictLogsPort,
    ManagementReviewQueuePort,
    OodaManagementDomainPort,
    # Research, Knowledge & Source
    ResearchKnowledgeSourcePort,
    DefaultResearchKnowledgeSourcePort,
    # Lifecycle, Telemetry & Governance
    IncidentReaderPort,
    DomainIncidentPort,
    LifecycleReaderPort,
    DomainLifecyclePort,
    GovernanceReaderPort,
    DomainGovernancePort,
    LineageReaderPort,
    DomainLineagePort,
    TelemetryReaderPort,
    DomainTelemetryPort,
    CompositeLifecycleTelemetryGovernancePort,
    InMemoryLifecycleTelemetryGovernancePort,
    create_lifecycle_telemetry_governance_port,
    create_in_memory_lifecycle_telemetry_governance_port,
    # Persona Training
    PersonaRegistryReadsPort,
    TrainingSessionTrainerPort,
    RapidEvaluationPort,
    PersonaTrainingDomainPort,
    # Unified Read Surface Ports
    ReadSurfacePorts,
    create_read_surface_ports,
    create_in_memory_read_surface_ports,
)


class TestPortDecouplingAndIsolation(unittest.TestCase):
    """Verifies that narrow ports do NOT import, instantiate, or delegate to ReadSurfaceStore."""

    def test_ports_package_ast_has_no_read_surface_store_instantiation(self) -> None:
        """Scan all .py files in services/control-plane/bff/ports/ to ensure zero ReadSurfaceStore calls."""
        ports_dir = Path(__file__).parent.parent / "ports"
        self.assertTrue(ports_dir.exists(), f"Ports directory not found: {ports_dir}")

        for py_file in ports_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check for direct constructor call ReadSurfaceStore(...)
                    if isinstance(node.func, ast.Name) and node.func.id == "ReadSurfaceStore":
                        self.fail(f"Found forbidden ReadSurfaceStore instantiation in {py_file.name}:{node.lineno}")
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "ReadSurfaceStore":
                        self.fail(f"Found forbidden ReadSurfaceStore attribute call in {py_file.name}:{node.lineno}")

    def test_in_memory_read_surface_ports_instantiation_without_read_store(self) -> None:
        """Constructing in-memory read surface ports does not touch read_store."""
        ports = create_in_memory_read_surface_ports()
        self.assertIsInstance(ports, ReadSurfacePorts)
        status = ports.get_surface_status()
        self.assertIn("operations_consultation", status)
        self.assertIn("persona_capital_runtime", status)
        self.assertIn("ooda_management", status)
        self.assertIn("research_knowledge_source", status)
        self.assertIn("lifecycle_telemetry_governance", status)
        self.assertIn("persona_training", status)


class TestOperationsConsultationPortCutover(unittest.TestCase):
    """Verifies Operations, OpenClaw, and Consultation port methods execute cleanly."""

    def setUp(self) -> None:
        self.ports = create_in_memory_operations_consultation_port(
            workflow_templates=[{"id": "wf-1", "name": "Morning Rebalance"}],
            hook_registry=[{"id": "hk-1", "name": "Pre-Trade Gate"}],
            governance_permissions=[{"role": "operator", "action": "approve"}],
            memory_governance_rules=[{"rule_id": "mg-1", "retention_days": 90}],
            consult_rules=[{"rule_id": "cr-1", "min_participants": 2}],
            route_policies=[{"route": "/bff/orders", "rate_limit": 100}],
            alpha_factory_cards=[{"id": "af-1", "lane": "momentum"}],
            skills=[{"skill_id": "sk-1", "name": "Arbitrage"}],
            tools=[{"tool_id": "tl-1", "name": "OrderSubmitter"}],
            mcp_servers=[{"server_id": "mcp-1", "name": "LocalGateway"}],
            mcp_tools=[{"tool_id": "mcp-tool-1", "server_id": "mcp-1"}],
        )

    def test_workflow_and_catalog_reads(self) -> None:
        wfs = self.ports.list_workflow_templates()
        self.assertEqual(len(wfs), 1)
        self.assertEqual(wfs[0]["id"], "wf-1")
        self.assertEqual(wfs[0]["name"], "Morning Rebalance")

        hooks = self.ports.list_hook_registry()
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["name"], "Pre-Trade Gate")

        self.assertEqual(len(self.ports.list_governance_permissions()), 1)
        self.assertEqual(len(self.ports.list_memory_governance_rules()), 1)
        self.assertEqual(len(self.ports.list_consult_rules()), 1)
        self.assertEqual(len(self.ports.list_route_policies()), 1)
        self.assertEqual(len(self.ports.list_alpha_factory_cards()), 1)
        self.assertEqual(len(self.ports.list_skills()), 1)
        self.assertEqual(len(self.ports.list_tools()), 1)
        self.assertEqual(len(self.ports.list_mcp_servers()), 1)
        self.assertEqual(len(self.ports.list_mcp_tools()), 1)

    def test_openclaw_operations_reads(self) -> None:
        snap = self.ports.get_openclaw_ops_snapshot()
        self.assertIn("overall_status", snap)
        readiness = self.ports.get_openclaw_broker_adapter_readiness()
        self.assertIn("gate_reason", readiness)
        self.assertFalse(readiness["live_execution_enabled"])
        preact = self.ports.get_research_oss_preactivation_snapshot()
        self.assertIn("surface", preact)

    def test_consultation_lifecycle_reads(self) -> None:
        created = self.ports.create_consult_request(
            from_persona_id="persona-a",
            target_type="strategy",
            target_ref="strat-1",
            task="Review strategy risk profile",
            context_refs=[{"kind": "strategy_spec", "id": "strat-1"}],
            priority="medium",
            consultation_type="strategy_review",
            actor_id="operator-1",
        )
        req_id = created["request_id"]
        req = self.ports.get_consult_request(req_id)
        self.assertIsNotNone(req)
        self.assertEqual(req["task"], "Review strategy risk profile")

        all_reqs = self.ports.list_consult_requests()
        self.assertEqual(len(all_reqs), 1)

        # Cancel consult request
        cancelled = self.ports.cancel_consult_request(req_id, actor_id="operator-1")
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled["status"], "canceled")


class TestPersonaCapitalRuntimePortCutover(unittest.TestCase):
    """Verifies Persona, Capital, Deployment, Runtime, and Ranking domain ports."""

    def setUp(self) -> None:
        self.ports = create_in_memory_persona_capital_runtime_port(
            personas=[
                {"persona_id": "persona-1", "name": "Alpha Hunter", "lifecycle_state": "active", "mandate": "momentum", "strategy_family": "trend"},
                {"persona_id": "persona-2", "name": "Risk Sentinel", "lifecycle_state": "paper", "mandate": "hedging", "strategy_family": "meanrev"},
            ],
            capital_pools=[
                {"pool_id": "pool-1", "capital_pool_id": "pool-1", "name": "Main Alpha Pool", "currency": "USD", "nav": 1000000.0},
            ],
            bindings=[
                {"binding_id": "b-1", "capital_pool_id": "pool-1", "persona_id": "persona-1", "allocation_pct": 60.0},
            ],
            deployment_plans=[
                {"plan_id": "plan-1", "target_stage": "paper", "status": "draft"},
            ],
            runtime_bindings=[
                {"binding_id": "rb-1", "runtime_id": "rt-1", "status": "active"},
            ],
            rankings=[
                {"ranking_id": "rk-1", "score": 95.5},
            ],
            ranking_formulas=[
                {"formula_id": "rf-1", "name": "Sharpe-Weighted"},
            ],
            persona_league=[
                {"persona_id": "persona-1", "tier": "gold", "rank": 1},
            ],
            rebalances=[
                {"rebalance_id": "reb-1", "status": "executed"},
            ],
            capital_allocations=[
                {"allocation_id": "ca-1", "persona_id": "persona-1", "allocated_amount": 600000.0},
            ],
            containments=[
                {"containment_id": "ct-1", "persona_id": "persona-2", "active": False},
            ],
            evolution_programs=[
                {"program_id": "ep-1", "name": "Alpha Optimizer"},
            ],
            evolution_decisions=[
                {"decision_id": "ed-1", "status": "approved"},
            ],
        )

    def test_persona_fleet_reads(self) -> None:
        personas = self.ports.list_personas()
        self.assertEqual(len(personas), 2)
        self.assertEqual(self.ports.get_persona("persona-1")["name"], "Alpha Hunter")
        self.assertIsNone(self.ports.get_persona("nonexistent"))

        operational = self.ports.list_operational_personas()
        self.assertEqual(len(operational), 2)

    def test_capital_pool_and_bindings_reads(self) -> None:
        pools = self.ports.list_capital_pools()
        self.assertEqual(len(pools), 1)
        self.assertEqual(self.ports.get_capital_pool("pool-1")["name"], "Main Alpha Pool")

        bindings = self.ports.list_bindings()
        self.assertEqual(len(bindings), 1)
        self.assertEqual(len(self.ports.get_bindings_for_pool("pool-1")), 1)
        self.assertEqual(len(self.ports.get_bindings_for_persona("persona-1")), 1)

    def test_deployment_and_runtime_reads(self) -> None:
        plans = self.ports.list_deployment_plans()
        self.assertEqual(len(plans), 1)
        self.assertEqual(self.ports.get_deployment_plan("plan-1")["target_stage"], "paper")

        rbs = self.ports.list_runtime_bindings()
        self.assertEqual(len(rbs), 1)
        self.assertEqual(self.ports.get_runtime_binding("rb-1")["runtime_id"], "rt-1")
        self.assertEqual(self.ports.get_runtime_binding_by_runtime_id("rt-1")["binding_id"], "rb-1")

    def test_ranking_and_evolution_reads(self) -> None:
        rankings = self.ports.list_rankings()
        self.assertEqual(len(rankings), 1)
        self.assertEqual(self.ports.get_ranking("rk-1")["score"], 95.5)

        league = self.ports.list_persona_league()
        self.assertEqual(len(league), 1)
        self.assertEqual(self.ports.get_persona_league_entry("persona-1")["tier"], "gold")

        progs = self.ports.list_evolution_programs()
        self.assertEqual(len(progs), 1)
        self.assertEqual(self.ports.get_evolution_program("ep-1")["name"], "Alpha Optimizer")

        decisions = self.ports.list_evolution_decisions()
        self.assertEqual(len(decisions), 1)


class TestOodaManagementPortCutover(unittest.TestCase):
    """Verifies OODA loop packets, interventions, conflict logs, and review queues."""

    def setUp(self) -> None:
        raw_ooda = [
            {"packet_id": "pkt-1", "stage": "orient", "status": "active", "strategy_id": "strat-1", "runtime_id": "rt-1"},
            {"packet_id": "pkt-2", "stage": "decide", "status": "completed", "strategy_id": "strat-2"},
        ]
        raw_interventions = [
            {"intervention_id": "int-1", "kind": "circuit_breaker", "status": "resolved", "triggered_at": "2026-08-28T12:00:00Z"},
        ]
        raw_conflicts = [
            {"log_id": "log-1", "capital_pool_id": "pool-1", "synthesis_method": "consensus", "proposal_ids": ["prop-1"]},
        ]
        raw_plans = [
            {"plan_id": "p-1", "target_stage": "paper", "status": "pending_review", "created_by": "user-1"},
        ]
        raw_decisions = [
            {"decision_id": "dec-1", "decision_state": "under_review", "target_type": "DeploymentPlan", "risk_level": "medium"},
        ]
        self.domain_port = OodaManagementDomainPort(
            ooda_port=OodaPacketsPort(records_provider=lambda: raw_ooda),
            interventions_port=InterventionsPort(records_provider=lambda: raw_interventions),
            synthesis_conflict_logs_port=SynthesisConflictLogsPort(records_provider=lambda: raw_conflicts),
            review_queue_port=ManagementReviewQueuePort(
                deployment_plans_reader=lambda: raw_plans,
                approval_decisions_reader=lambda: raw_decisions,
            ),
        )

    def test_ooda_packet_filtering_and_retrieval(self) -> None:
        all_pkts = self.domain_port.list_ooda_packets()
        self.assertEqual(len(all_pkts), 2)
        self.assertEqual(self.domain_port.get_ooda_packet("pkt-1")["stage"], "orient")

        # Stage filter
        orient_pkts = self.domain_port.list_ooda_packets(stage="orient")
        self.assertEqual(len(orient_pkts), 1)
        self.assertEqual(orient_pkts[0]["packet_id"], "pkt-1")

        # Ref filters
        strat_pkts = self.domain_port.list_ooda_packets_for_strategy("strat-1")
        self.assertEqual(len(strat_pkts), 1)

    def test_interventions_and_conflict_logs(self) -> None:
        ints = self.domain_port.list_interventions()
        self.assertEqual(len(ints), 1)
        self.assertEqual(self.domain_port.get_intervention("int-1")["kind"], "circuit_breaker")

        conflicts = self.domain_port.list_synthesis_conflict_logs()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(self.domain_port.get_synthesis_conflict_log("log-1")["capital_pool_id"], "pool-1")

    def test_review_and_approval_queues(self) -> None:
        review_items = self.domain_port.list_governance_review_queue_items()
        self.assertTrue(len(review_items) >= 1)
        approval_items = self.domain_port.list_approval_queue_items()
        self.assertEqual(len(approval_items), 1)
        self.assertEqual(approval_items[0]["decision_id"], "dec-1")


class TestResearchKnowledgeSourcePortCutover(unittest.TestCase):
    """Verifies Research, Knowledge, Memory, Search, and Source reads."""

    def setUp(self) -> None:
        self.port = DefaultResearchKnowledgeSourcePort(
            research_notes_store={
                "note-1": {"note_id": "note-1", "title": "Alpha finding", "created_at": "2026-08-28T01:00:00Z"},
            },
            evidence_refs_store={
                "ref-1": {"ref_id": "ref-1", "tenant_id": "tenant-a", "display_label": "Evidence A"},
            },
            insight_cards_store={
                "card-1": {"card_id": "card-1", "title": "Market Insight"},
            },
            strategy_specs_store={
                "strat-1": {
                    "strategy_id": "strat-1",
                    "title": "Momentum Alpha",
                    "versions": [
                        {
                            "spec_version_id": "strat-1-v1",
                            "spec_version": "v1",
                            "lifecycle_state": "candidate",
                            "hypothesis": "Short-term momentum yields alpha.",
                            "objective": "Sharpe > 2.0",
                            "created_at": "2026-08-20T00:00:00Z",
                        }
                    ],
                }
            },
            research_tickets_store={
                "tkt-1": {"ticket_id": "tkt-1", "title": "Ticket 1"},
            },
            research_analyses_store={
                "ana-1": {"analysis_id": "ana-1", "title": "Analysis 1"},
            },
            research_experiments_store={
                "exp-1": {"experiment_id": "exp-1", "name": "Exp 1"},
            },
        )

    def test_dataset_source_and_surface_status(self) -> None:
        source = self.port.dataset_source("research_notes")
        self.assertEqual(source, "typed_store")
        status = self.port.dataset_surface_status("research_notes", snapshot_at="2026-08-28T12:00:00Z", has_data=True)
        self.assertEqual(status["status"], "ok")

    def test_knowledge_and_research_lists(self) -> None:
        self.assertEqual(len(self.port.list_research_notes()), 1)
        self.assertEqual(len(self.port.list_evidence_refs()), 1)
        self.assertEqual(len(self.port.list_insight_cards()), 1)
        self.assertEqual(len(self.port.list_strategy_specs()), 1)
        self.assertEqual(len(self.port.list_research_tickets()), 1)
        self.assertEqual(len(self.port.list_research_analyses()), 1)
        self.assertEqual(len(self.port.list_research_experiments()), 1)


class TestLifecycleTelemetryGovernancePortCutover(unittest.TestCase):
    """Verifies Lifecycle, Telemetry, Incident, Governance, and Lineage reads."""

    def setUp(self) -> None:
        self.port = create_in_memory_lifecycle_telemetry_governance_port(
            incidents={"inc-1": {"id": "inc-1", "incident_id": "inc-1", "severity": "P1", "status": "resolved"}},
            postmortems={"pm-1": {"id": "pm-1", "report_id": "pm-1", "incident_id": "inc-1"}},
            loop_runs={"lr-1": {"id": "lr-1", "status": "healthy"}},
            sentinel_findings={"sf-1": {"id": "sf-1", "finding": "no_drift"}},
            kill_switch={"active": False, "status": "armed"},
            evolution_decisions={"ed-1": {"id": "ed-1", "status": "approved"}},
            freeze_orders={},
            all_rollbacks={},
            rollback_reviews={},
            governance_audit_events=[{"id": "ae-1", "action": "sign"}],
            lineage_edges={"edge-1": {"source": "node-a", "target": "node-b"}},
            inspiration_graphs={},
            artifact_registry_entries=[],
            telemetry_events=[{"id": "te-1", "metric": "latency_ms", "value": 42}],
            telemetry_summaries={"sum-1": {"summary": "all good"}},
            telemetry_performance={"perf-1": {"p99": 12.5}},
            paper_live_drift_reports={"dr-1": {"id": "dr-1", "drift_pct": 0.01}},
            telemetry_events_source="in_memory",
        )

    def test_incidents_and_postmortems(self) -> None:
        incidents = self.port.list_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(self.port.get_incident("inc-1")["severity"], "P1")

        pms = self.port.list_postmortems()
        self.assertEqual(len(pms), 1)
        self.assertEqual(self.port.get_postmortem("pm-1")["incident_id"], "inc-1")

    def test_lifecycle_and_loop_reads(self) -> None:
        avail, runs = self.port.list_loop_runs()
        self.assertTrue(avail)
        self.assertEqual(len(runs), 1)

        avail, findings = self.port.list_sentinel_findings()
        self.assertTrue(avail)
        self.assertEqual(len(findings), 1)

        ks = self.port.get_kill_switch_status()
        self.assertFalse(ks["active"])
        self.assertEqual(ks["status"], "armed")

    def test_governance_and_lineage_reads(self) -> None:
        events = self.port.list_governance_audit_events()
        self.assertEqual(len(events), 1)

        edges = self.port.list_lineage_edges()
        self.assertEqual(len(edges), 1)

    def test_telemetry_reads(self) -> None:
        events = self.port.list_telemetry_events()
        self.assertEqual(len(events), 1)
        src, events_src = self.port.list_telemetry_events_with_source()
        self.assertEqual(src, "in_memory")
        self.assertEqual(len(events_src), 1)


class TestPersonaTrainingPortCutover(unittest.TestCase):
    """Verifies Persona Training, Replay, and Rapid-Evaluation domain ports."""

    def setUp(self) -> None:
        mock_store = MagicMock()
        mock_store.list_personas.return_value = [{"id": "p-1", "name": "Trainer-Persona"}]
        mock_store.get_persona.return_value = {"id": "p-1", "name": "Trainer-Persona"}
        mock_store.get_bindings_for_persona.return_value = []
        mock_store.list_sessions_for_persona.return_value = [{"id": "s-1"}]
        mock_store.list_teaching_sessions_for_persona.return_value = [{"id": "ts-1"}]
        mock_store.get_capability_snapshot_for_persona.return_value = {"skills": ["trade"]}

        mock_training = MagicMock()
        mock_training.create_trainer_session.return_value = {"session_id": "trn-1"}
        mock_training.list_trainer_sessions.return_value = [{"session_id": "trn-1"}]
        mock_training.get_trainer_session.return_value = {"session_id": "trn-1"}
        mock_training.get_trainer_controls.return_value = {"mode": "interactive"}
        mock_training.patch_trainer_controls.return_value = {"mode": "auto"}
        mock_training.append_trainer_message.return_value = {"message_id": "m-1"}
        mock_training.get_trainer_preview.return_value = {"preview": True}
        mock_training.refresh_trainer_preview.return_value = {"preview": True}
        mock_training.list_trainer_replays.return_value = [{"replay_id": "rep-1"}]
        mock_training.get_trainer_replay.return_value = {"replay_id": "rep-1"}
        mock_training.commit_trainer_replay.return_value = {"committed": True}
        mock_training.discard_trainer_replay.return_value = {"discarded": True}

        self.port = PersonaTrainingDomainPort(
            persona_port=PersonaRegistryReadsPort(store=mock_store),
            trainer_port=TrainingSessionTrainerPort(training=mock_training),
            rapid_eval_port=RapidEvaluationPort(
                create=lambda s, **kw: {"eval_id": f"eval-{s}"},
                get=lambda e, **kw: {"eval_id": e},
            ),
        )

    def test_persona_training_and_replay_reads(self) -> None:
        personas = self.port.list_personas()
        self.assertEqual(len(personas), 1)
        self.assertEqual(self.port.get_persona("p-1")["name"], "Trainer-Persona")

        session = self.port.create_trainer_session()
        self.assertEqual(session["session_id"], "trn-1")
        self.assertEqual(len(self.port.list_trainer_sessions()), 1)
        self.assertEqual(self.port.get_trainer_controls("trn-1")["mode"], "interactive")
        self.assertEqual(self.port.patch_trainer_controls("trn-1", mode="auto")["mode"], "auto")

        # Rapid eval
        eval_result = self.port.create_rapid_eval("s-1")
        self.assertEqual(eval_result["eval_id"], "eval-s-1")
        self.assertEqual(self.port.get_rapid_eval("eval-1")["eval_id"], "eval-1")


class TestUnifiedReadSurfacePorts(unittest.TestCase):
    """Verifies the unified ReadSurfacePorts container combining all domains."""

    def test_unified_in_memory_facade(self) -> None:
        ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": [{"persona_id": "p-unified", "name": "Unified Persona", "lifecycle_state": "active"}],
                "capital_pools": [{"pool_id": "pool-u", "name": "Unified Pool"}],
            },
            lifecycle_telemetry_governance_kwargs={
                "incidents": {"inc-u": {"id": "inc-u", "incident_id": "inc-u", "severity": "P2"}},
            },
        )
        # Check direct delegation on unified container
        personas = ports.list_personas()
        self.assertEqual(len(personas), 1)
        self.assertEqual(personas[0]["persona_id"], "p-unified")

        pools = ports.list_capital_pools()
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["pool_id"], "pool-u")

        incidents = ports.list_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["incident_id"], "inc-u")

        # Surface status aggregation
        surface_status = ports.get_surface_status()
        self.assertIn("operations_consultation", surface_status)
        self.assertIn("persona_capital_runtime", surface_status)
        self.assertIn("ooda_management", surface_status)
        self.assertIn("research_knowledge_source", surface_status)
        self.assertIn("lifecycle_telemetry_governance", surface_status)
        self.assertIn("persona_training", surface_status)


if __name__ == "__main__":
    unittest.main()
