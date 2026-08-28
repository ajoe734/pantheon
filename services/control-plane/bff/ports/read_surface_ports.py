"""Unified Read Surface Ports container and factories.

This module provides `ReadSurfacePorts`, a unified port container combining all
six narrow domain ports (Operations/Consultation, Persona/Capital/Runtime,
OODA/Management, Research/Knowledge/Source, Lifecycle/Telemetry/Governance,
and Persona Training/Replay).

Crucially, this module does NOT import, instantiate, or delegate to `ReadSurfaceStore`.
Every method is cleanly resolved through its respective domain port.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from ports.operations_consultation import (
        CompositeOperationsConsultationPort,
        DomainConsultationPort,
        DomainOpenClawOperationsPort,
        DomainWorkflowCatalogPort,
        InMemoryOperationsConsultationPort,
        OperationsConsultationPort,
        create_in_memory_operations_consultation_port,
        create_operations_consultation_port,
    )
    from ports.persona_capital_runtime import (
        CapitalPoolPort,
        CompositePersonaCapitalRuntimePort,
        DeploymentPlanPort,
        EvolutionProjectionPort,
        InMemoryPersonaCapitalRuntimePort,
        PersonaCapitalRuntimeDomainPort,
        PersonaFleetPort,
        RankingProjectionPort,
        RuntimePort,
        create_in_memory_persona_capital_runtime_port,
        create_persona_capital_runtime_port,
    )
    from ports.ooda_management import (
        InterventionsPort,
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
        OodaPacketsPort,
        SynthesisConflictLogsPort,
    )
    from ports.research_knowledge_source import (
        DefaultResearchKnowledgeSourcePort,
        ResearchKnowledgeSourcePort,
    )
    from ports.lifecycle_telemetry_governance import (
        CompositeLifecycleTelemetryGovernancePort,
        DomainGovernancePort,
        DomainIncidentPort,
        DomainLifecyclePort,
        DomainLineagePort,
        DomainTelemetryPort,
        InMemoryLifecycleTelemetryGovernancePort,
        create_in_memory_lifecycle_telemetry_governance_port,
        create_lifecycle_telemetry_governance_port,
    )
    from ports.persona_training import (
        PersonaRegistryReadsPort,
        PersonaTrainingDomainPort,
        RapidEvaluationPort,
        TrainingSessionTrainerPort,
    )
except ImportError:
    from domain_ports.operations_consultation import (  # type: ignore[no-redef]
        CompositeOperationsConsultationPort,
        DomainConsultationPort,
        DomainOpenClawOperationsPort,
        DomainWorkflowCatalogPort,
        InMemoryOperationsConsultationPort,
        OperationsConsultationPort,
        create_in_memory_operations_consultation_port,
        create_operations_consultation_port,
    )
    from domain_ports.persona_capital_runtime import (  # type: ignore[no-redef]
        CapitalPoolPort,
        DeploymentPlanPort,
        EvolutionProjectionPort,
        PersonaCapitalRuntimeDomainPort,
        PersonaFleetPort,
        RankingProjectionPort,
        RuntimePort,
    )
    from domain_ports.ooda_management import (  # type: ignore[no-redef]
        InterventionsPort,
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
        OodaPacketsPort,
        SynthesisConflictLogsPort,
    )
    from domain_ports.research_knowledge_source import (  # type: ignore[no-redef]
        DefaultResearchKnowledgeSourcePort,
        ResearchKnowledgeSourcePort,
    )
    from domain_ports.lifecycle_telemetry_governance import (  # type: ignore[no-redef]
        CompositeLifecycleTelemetryGovernancePort,
        DomainGovernancePort,
        DomainIncidentPort,
        DomainLifecyclePort,
        DomainLineagePort,
        DomainTelemetryPort,
        InMemoryLifecycleTelemetryGovernancePort,
        create_in_memory_lifecycle_telemetry_governance_port,
        create_lifecycle_telemetry_governance_port,
    )
    from domain_ports.persona_training import (  # type: ignore[no-redef]
        PersonaRegistryReadsPort,
        PersonaTrainingDomainPort,
        RapidEvaluationPort,
        TrainingSessionTrainerPort,
    )


class ReadSurfacePorts:
    """Unified container for all narrow read-surface domain ports.

    Decouples callers completely from ReadSurfaceStore by providing direct
    typed domain ports and ergonomic delegation to domain read methods.
    """

    def __init__(
        self,
        *,
        operations_consultation: Optional[OperationsConsultationPort] = None,
        persona_capital_runtime: Optional[Union[CompositePersonaCapitalRuntimePort, PersonaCapitalRuntimeDomainPort]] = None,
        ooda_management: Optional[OodaManagementDomainPort] = None,
        research_knowledge_source: Optional[ResearchKnowledgeSourcePort] = None,
        lifecycle_telemetry_governance: Optional[CompositeLifecycleTelemetryGovernancePort] = None,
        persona_training: Optional[PersonaTrainingDomainPort] = None,
    ) -> None:
        self.operations_consultation = operations_consultation or create_operations_consultation_port()
        self.persona_capital_runtime = persona_capital_runtime or create_persona_capital_runtime_port()
        self.ooda_management = ooda_management or OodaManagementDomainPort()
        self.research_knowledge_source = research_knowledge_source or DefaultResearchKnowledgeSourcePort()
        self.lifecycle_telemetry_governance = lifecycle_telemetry_governance or create_lifecycle_telemetry_governance_port()
        self.persona_training = persona_training or PersonaTrainingDomainPort()

    def __getattr__(self, name: str) -> Any:
        for port in (
            self.operations_consultation,
            self.persona_capital_runtime,
            self.ooda_management,
            self.research_knowledge_source,
            self.lifecycle_telemetry_governance,
            self.persona_training,
        ):
            if hasattr(port, name):
                return getattr(port, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in (
            "operations_consultation",
            "persona_capital_runtime",
            "ooda_management",
            "research_knowledge_source",
            "lifecycle_telemetry_governance",
            "persona_training",
        ):
            super().__setattr__(name, value)
            return
        if name == "_trade_journey_projection_reader_override":
            if hasattr(self, "lifecycle_telemetry_governance") and hasattr(self.lifecycle_telemetry_governance, "lifecycle"):
                setattr(self.lifecycle_telemetry_governance.lifecycle, "_projection_reader_override", value)
                setattr(self.lifecycle_telemetry_governance, "_projection_reader_override", value)
        super().__setattr__(name, value)

    # -------------------------------------------------------------------------
    # Surface Status & Diagnostics
    # -------------------------------------------------------------------------
    def get_surface_status(self) -> Dict[str, Any]:
        """Aggregate diagnostics across all six narrow domain ports."""
        status_ops = (
            self.operations_consultation.get_surface_status()
            if hasattr(self.operations_consultation, "get_surface_status")
            else {"status": "ok"}
        )
        status_pcr = (
            self.persona_capital_runtime.get_surface_status()
            if hasattr(self.persona_capital_runtime, "get_surface_status")
            else {"status": "ok"}
        )
        status_ooda = {
            "ooda": (
                self.ooda_management.ooda.get_surface_status()
                if hasattr(self.ooda_management, "ooda") and hasattr(self.ooda_management.ooda, "get_surface_status")
                else {"status": "ok"}
            ),
            "interventions": (
                self.ooda_management.interventions.get_surface_status()
                if hasattr(self.ooda_management, "interventions") and hasattr(self.ooda_management.interventions, "get_surface_status")
                else {"status": "ok"}
            ),
        }
        status_ltg = (
            self.lifecycle_telemetry_governance.get_surface_status()
            if hasattr(self.lifecycle_telemetry_governance, "get_surface_status")
            else {"status": "ok"}
        )
        return {
            "operations_consultation": status_ops,
            "persona_capital_runtime": status_pcr,
            "ooda_management": status_ooda,
            "research_knowledge_source": {"status": "ok"},
            "lifecycle_telemetry_governance": status_ltg,
            "persona_training": {"status": "ok"},
        }

    # -------------------------------------------------------------------------
    # Operations & Consultation Delegates
    # -------------------------------------------------------------------------
    def list_workflow_templates(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_workflow_templates(**kwargs)

    def list_hook_registry(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_hook_registry(**kwargs)

    def list_governance_permissions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_governance_permissions(**kwargs)

    def list_memory_governance_rules(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_memory_governance_rules(**kwargs)

    def list_consult_rules(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_consult_rules(**kwargs)

    def list_route_policies(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_route_policies(**kwargs)

    def list_alpha_factory_cards(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_alpha_factory_cards(**kwargs)

    def list_skills(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_skills(**kwargs)

    def list_tools(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_tools(**kwargs)

    def list_mcp_servers(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_mcp_servers(**kwargs)

    def list_mcp_tools(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_mcp_tools(**kwargs)

    def get_openclaw_ops_snapshot(self, **kwargs: Any) -> Dict[str, Any]:
        return self.operations_consultation.get_openclaw_ops_snapshot(**kwargs)

    def get_openclaw_broker_adapter_readiness(self, **kwargs: Any) -> Dict[str, Any]:
        return self.operations_consultation.get_openclaw_broker_adapter_readiness(**kwargs)

    def get_research_oss_preactivation_snapshot(self, **kwargs: Any) -> Dict[str, Any]:
        return self.operations_consultation.get_research_oss_preactivation_snapshot(**kwargs)

    def list_consult_requests(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_consult_requests(**kwargs)

    def get_consult_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self.operations_consultation.get_consult_request(request_id)

    def create_consult_request(self, **kwargs: Any) -> Dict[str, Any]:
        return self.operations_consultation.create_consult_request(**kwargs)

    def cancel_consult_request(self, request_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.operations_consultation.cancel_consult_request(request_id, **kwargs)

    def list_consult_memos(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_consult_memos(**kwargs)

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.operations_consultation.get_consult_memo(memo_id)

    def list_consultations_for_persona(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_consultations_for_persona(persona_id, **kwargs)

    def get_consultation(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.operations_consultation.get_consultation(session_id)

    def get_consultation_participants(self, session_id: str) -> List[Dict[str, Any]]:
        return self.operations_consultation.get_consultation_participants(session_id)

    def get_consultation_outcome(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.operations_consultation.get_consultation_outcome(session_id)

    def get_consultation_evidence(self, session_id: str) -> List[Dict[str, Any]]:
        return self.operations_consultation.get_consultation_evidence(session_id)

    def get_consult_transcript(self, session_id: str, **kwargs: Any) -> Dict[str, Any]:
        return self.operations_consultation.get_consult_transcript(session_id, **kwargs)

    # -------------------------------------------------------------------------
    # Persona, Capital, and Runtime Delegates
    # -------------------------------------------------------------------------
    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_personas(**kwargs)

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_persona(persona_id)

    def list_operational_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_operational_personas()

    def list_capital_pools(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_capital_pools(**kwargs)

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_capital_pool(pool_id)

    def list_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_bindings(**kwargs)

    def get_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_binding(binding_id)

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.get_bindings_for_pool(pool_id)

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.get_bindings_for_persona(persona_id)

    def list_deployment_plans(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_deployment_plans(**kwargs)

    def get_deployment_plan(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_deployment_plan(plan_id)

    def list_runtime_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_runtime_bindings(**kwargs)

    def get_runtime_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_runtime_binding(binding_id)

    def get_runtime_binding_by_runtime_id(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_runtime_binding_by_runtime_id(runtime_id)

    def list_rankings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_rankings(**kwargs)

    def get_ranking(self, ranking_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_ranking(ranking_id)

    def list_ranking_formulas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_ranking_formulas(**kwargs)

    def list_persona_league(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_persona_league(**kwargs)

    def get_persona_league_entry(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_persona_league_entry(persona_id)

    def list_rebalances(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_rebalances(**kwargs)

    def get_rebalance(self, rebalance_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_rebalance(rebalance_id)

    def list_capital_allocations(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_capital_allocations(**kwargs)

    def list_containments(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_containments(**kwargs)

    def get_persona_containment(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_persona_containment(persona_id)

    def build_persona_capital_ranking_view(self, persona_id: Optional[str]) -> Dict[str, Any]:
        return self.persona_capital_runtime.build_persona_capital_ranking_view(persona_id)

    def list_evolution_programs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_evolution_programs(**kwargs)

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_capital_runtime.get_evolution_program(program_id)

    def list_evolution_decisions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_evolution_decisions(**kwargs)

    def list_evolution_program_runs(self, program_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_evolution_program_runs(program_id)

    def list_evolution_program_candidates(self, program_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.persona_capital_runtime.list_evolution_program_candidates(program_id)

    # -------------------------------------------------------------------------
    # OODA & Management Delegates
    # -------------------------------------------------------------------------
    def list_ooda_packets(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda_management.list_ooda_packets(**kwargs)

    def get_ooda_packet(self, packet_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ooda_management.get_ooda_packet(packet_id)

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        return self.ooda_management.list_ooda_packets_for_strategy(strategy_id)

    def list_ooda_packets_for_runtime(self, runtime_id: str) -> List[Dict[str, Any]]:
        return self.ooda_management.list_ooda_packets_for_runtime(runtime_id)

    def list_ooda_packets_for_evolution_program(self, program_id: str) -> List[Dict[str, Any]]:
        return self.ooda_management.list_ooda_packets_for_evolution_program(program_id)

    def list_interventions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda_management.list_interventions(**kwargs)

    def get_intervention(self, intervention_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ooda_management.get_intervention(intervention_id)

    def list_synthesis_conflict_logs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda_management.list_synthesis_conflict_logs(**kwargs)

    def get_synthesis_conflict_log(self, log_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ooda_management.get_synthesis_conflict_log(log_id)

    def list_governance_review_queue_items(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda_management.list_governance_review_queue_items(**kwargs)

    def list_approval_queue_items(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda_management.list_approval_queue_items(**kwargs)

    def get_deployment_diff(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ooda_management.get_deployment_diff(plan_id)

    # -------------------------------------------------------------------------
    # Research, Knowledge, and Source Delegates
    # -------------------------------------------------------------------------
    def dataset_source(self, dataset: str) -> str:
        return self.research_knowledge_source.dataset_source(dataset)

    def dataset_surface_status(self, dataset: str, **kwargs: Any) -> Dict[str, Any]:
        return self.research_knowledge_source.dataset_surface_status(dataset, **kwargs)

    def list_research_notes(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_notes(**kwargs)

    def get_research_note(self, note_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_note(note_id)

    def list_evidence_refs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_evidence_refs(**kwargs)

    def get_evidence_ref(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_evidence_ref(evidence_id)

    def list_insight_cards(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_insight_cards(**kwargs)

    def get_insight_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_insight_card(card_id)

    def list_strategy_specs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_strategy_specs(**kwargs)

    def get_strategy_spec(self, spec_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_strategy_spec(spec_id)

    def list_institutional_memory(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_institutional_memory(**kwargs)

    def get_institutional_memory(self, entry_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_institutional_memory(entry_id)

    def list_research_tickets(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_tickets(**kwargs)

    def get_research_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_ticket(ticket_id)

    def list_research_analyses(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_analyses(**kwargs)

    def get_research_analysis(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_analysis(analysis_id)

    def list_research_experiments(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_experiments(**kwargs)

    def get_research_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_experiment(experiment_id)

    def list_research_artifact_comparisons(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_artifact_comparisons(**kwargs)

    def get_research_artifact_comparison(self, comparison_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_artifact_comparison(comparison_id)

    def list_data_sources(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_data_sources(**kwargs)

    def get_data_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_data_source(source_id)

    def search(self, **kwargs: Any) -> Dict[str, Any]:
        return self.research_knowledge_source.search(**kwargs)

    # -------------------------------------------------------------------------
    # Lifecycle, Telemetry, and Governance Delegates
    # -------------------------------------------------------------------------
    def list_incidents(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_incidents(**kwargs)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_incident(incident_id)

    def list_postmortems(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_postmortems(**kwargs)

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_postmortem(report_id)

    def list_loop_runs(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return self.lifecycle_telemetry_governance.list_loop_runs()

    def list_sentinel_findings(self, **kwargs: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        return self.lifecycle_telemetry_governance.list_sentinel_findings(**kwargs)

    def get_kill_switch_status(self) -> Dict[str, Any]:
        return self.lifecycle_telemetry_governance.get_kill_switch_status()

    def get_kill_switch(self) -> Dict[str, Any]:
        return self.lifecycle_telemetry_governance.get_kill_switch_status()

    def list_governance_audit_events(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_governance_audit_events(**kwargs)

    def list_lineage_edges(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_lineage_edges(**kwargs)

    def get_lineage_graph(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_lineage_graph(**kwargs)

    def list_telemetry_events(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_telemetry_events(**kwargs)

    def list_telemetry_events_with_source(self, **kwargs: Any) -> Tuple[str, List[Dict[str, Any]]]:
        return self.lifecycle_telemetry_governance.list_telemetry_events_with_source(**kwargs)

    def get_telemetry_events_source(self) -> str:
        return self.lifecycle_telemetry_governance.get_telemetry_events_source()

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_telemetry_summary(runtime_id)

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_telemetry_performance(artifact_id)

    def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_paper_live_drift_reports()

    # -------------------------------------------------------------------------
    # Persona Training & Replay Delegates
    # -------------------------------------------------------------------------
    def list_persona_sessions(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_training.list_persona_sessions(persona_id, **kwargs)

    def list_persona_teaching_sessions(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_training.list_persona_teaching_sessions(persona_id, **kwargs)

    def get_persona_capabilities(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_persona_capabilities(persona_id)

    def create_trainer_session(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.create_trainer_session(**kwargs)

    def list_trainer_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_training.list_trainer_sessions(**kwargs)

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_trainer_session(session_id)

    def get_trainer_controls(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_trainer_controls(session_id)

    def patch_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.patch_trainer_controls(session_id, **kwargs)

    def append_trainer_message(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.append_trainer_message(session_id, **kwargs)

    def get_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_trainer_preview(session_id, **kwargs)

    def refresh_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.refresh_trainer_preview(session_id, **kwargs)

    def list_trainer_replays(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona_training.list_trainer_replays(**kwargs)

    def get_trainer_replay(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_trainer_replay(session_id)

    def commit_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.commit_trainer_replay(session_id, **kwargs)

    def discard_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.discard_trainer_replay(session_id, **kwargs)

    def create_rapid_eval(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.create_rapid_eval(session_id, **kwargs)

    def get_rapid_eval(self, eval_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_rapid_eval(eval_id, **kwargs)

    def get_capability_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if hasattr(self.persona_training, "get_capability_snapshot"):
            return self.persona_training.get_capability_snapshot(snapshot_id)
        return None

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if hasattr(self.persona_training, "get_capability_snapshot_for_persona"):
            return self.persona_training.get_capability_snapshot_for_persona(persona_id)
        if persona_id and hasattr(self.persona_training, "get_persona_capabilities"):
            return self.persona_training.get_persona_capabilities(persona_id)
        return None

    def trade_journey_projection_reader(self) -> Any:
        override = getattr(self, "_trade_journey_projection_reader_override", None)
        if override is not None:
            return override
        if hasattr(self.lifecycle_telemetry_governance, "trade_journey_projection_reader"):
            return self.lifecycle_telemetry_governance.trade_journey_projection_reader()
        return None


def create_read_surface_ports(
    *,
    operations_consultation: Optional[OperationsConsultationPort] = None,
    persona_capital_runtime: Optional[Union[CompositePersonaCapitalRuntimePort, PersonaCapitalRuntimeDomainPort]] = None,
    ooda_management: Optional[OodaManagementDomainPort] = None,
    research_knowledge_source: Optional[ResearchKnowledgeSourcePort] = None,
    lifecycle_telemetry_governance: Optional[CompositeLifecycleTelemetryGovernancePort] = None,
    persona_training: Optional[PersonaTrainingDomainPort] = None,
    **kwargs: Any,
) -> ReadSurfacePorts:
    """Factory creating a production-grade composite ReadSurfacePorts instance."""
    return ReadSurfacePorts(
        operations_consultation=operations_consultation,
        persona_capital_runtime=persona_capital_runtime,
        ooda_management=ooda_management,
        research_knowledge_source=research_knowledge_source,
        lifecycle_telemetry_governance=lifecycle_telemetry_governance,
        persona_training=persona_training,
    )


def create_in_memory_read_surface_ports(
    *,
    operations_consultation_kwargs: Optional[Dict[str, Any]] = None,
    persona_capital_runtime_kwargs: Optional[Dict[str, Any]] = None,
    ooda_management_kwargs: Optional[Dict[str, Any]] = None,
    research_knowledge_source_kwargs: Optional[Dict[str, Any]] = None,
    lifecycle_telemetry_governance_kwargs: Optional[Dict[str, Any]] = None,
    persona_training_kwargs: Optional[Dict[str, Any]] = None,
    **generic_kwargs: Any,
) -> ReadSurfacePorts:
    """Factory creating an in-memory test double ReadSurfacePorts instance."""
    ops_port = create_in_memory_operations_consultation_port(**(operations_consultation_kwargs or {}))
    pcr_port = create_in_memory_persona_capital_runtime_port(**(persona_capital_runtime_kwargs or {}))
    ooda_kw = dict(ooda_management_kwargs or {})
    if any(k in ooda_kw for k in ("ooda_packets", "interventions", "synthesis_conflict_logs", "governance_review_queue_items", "approval_queue_items", "deployment_diffs")):
        ooda_p = OodaPacketsPort(records_provider=lambda: list(ooda_kw.get("ooda_packets") or []))
        int_p = InterventionsPort(records_provider=lambda: list(ooda_kw.get("interventions") or []))
        scl_p = SynthesisConflictLogsPort(records_provider=lambda: list(ooda_kw.get("synthesis_conflict_logs") or []))
        rq_p = ManagementReviewQueuePort(
            deployment_plans_reader=lambda: list(ooda_kw.get("deployment_plans") or []),
            evolution_decisions_reader=lambda: list(ooda_kw.get("evolution_decisions") or []),
            approval_decisions_reader=lambda: list(ooda_kw.get("approval_decisions") or []),
            deployment_diffs_reader=lambda pid: (ooda_kw.get("deployment_diffs") or {}).get(pid),
        )
        ooda_port = OodaManagementDomainPort(
            ooda_port=ooda_p,
            interventions_port=int_p,
            synthesis_conflict_logs_port=scl_p,
            review_queue_port=rq_p,
        )
    else:
        ooda_port = OodaManagementDomainPort(**ooda_kw)
    rks_port = DefaultResearchKnowledgeSourcePort(**(research_knowledge_source_kwargs or {}))
    ltg_port = create_in_memory_lifecycle_telemetry_governance_port(**(lifecycle_telemetry_governance_kwargs or {}))
    pt_port = PersonaTrainingDomainPort(**(persona_training_kwargs or {}))
    return ReadSurfacePorts(
        operations_consultation=ops_port,
        persona_capital_runtime=pcr_port,
        ooda_management=ooda_port,
        research_knowledge_source=rks_port,
        lifecycle_telemetry_governance=ltg_port,
        persona_training=pt_port,
    )
