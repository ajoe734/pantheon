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

from services.control_plane.bff.ports.operations_consultation import (
    CompositeOperationsConsultationPort,
    DomainConsultationPort,
    DomainOpenClawOperationsPort,
    DomainWorkflowCatalogPort,
    InMemoryOperationsConsultationPort,
    OperationsConsultationPort,
    create_in_memory_operations_consultation_port,
    create_operations_consultation_port,
)
from services.control_plane.bff.ports.persona_capital_runtime import (
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
from services.control_plane.bff.ports.ooda_management import (
    InterventionsPort,
    ManagementReviewQueuePort,
    OodaManagementDomainPort,
    OodaPacketsPort,
    SynthesisConflictLogsPort,
)
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    ResearchKnowledgeSourcePort,
)
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
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
from services.control_plane.bff.ports.persona_training import (
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

    _active_delegate: Optional[Any] = None

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
        self._active_delegate = None
        self.operations_consultation = operations_consultation or create_operations_consultation_port()
        self.persona_capital_runtime = persona_capital_runtime or create_persona_capital_runtime_port()
        self.ooda_management = ooda_management or OodaManagementDomainPort()
        self.research_knowledge_source = research_knowledge_source or DefaultResearchKnowledgeSourcePort()
        self.lifecycle_telemetry_governance = lifecycle_telemetry_governance or create_lifecycle_telemetry_governance_port()
        self.persona_training = persona_training or PersonaTrainingDomainPort()


    def __setattr__(self, name: str, value: Any) -> None:
        if name in (
            "_active_delegate",
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

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("__") or name in (
            "_active_delegate",
            "_trade_journey_projection_reader_override",
            "operations_consultation",
            "persona_capital_runtime",
            "ooda_management",
            "research_knowledge_source",
            "lifecycle_telemetry_governance",
            "persona_training",
        ):
            return super().__getattribute__(name)

        delegate = super().__getattribute__("_active_delegate")
        if delegate is not None and delegate is not self and not name.startswith("_"):
            delegate_cls = type(delegate)
            if name in delegate.__dict__ or (hasattr(delegate_cls, name) and name in delegate_cls.__dict__):
                return getattr(delegate, name)
            if not isinstance(delegate, ReadSurfacePorts) and hasattr(delegate, name):
                return getattr(delegate, name)
        return super().__getattribute__(name)

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
    _OPERATIONS_CONSULTATION_OWNED_DATASETS = frozenset(
        (
            "consult_requests",
            "consult_memos",
            "consult_rules",
            "route_policies",
            "workflow_templates",
            "hook_registry",
            "governance_permissions",
            "memory_governance_rules",
            "alpha_factory_cards",
            "skills",
            "tools",
            "mcp_servers",
            "mcp_tools",
        )
    )

    def dataset_source(self, dataset: str) -> str:
        res = self.research_knowledge_source.dataset_source(dataset)
        if res != "missing":
            return res
        if dataset in self._OPERATIONS_CONSULTATION_OWNED_DATASETS:
            # Route to the actual owning port's truthful client/store/missing
            # state instead of a blanket "typed_store" default, so a missing
            # consultation client/store or catalog backend surfaces as
            # unavailable rather than a false healthy default.
            return self.operations_consultation.dataset_source(dataset)
        if dataset in (
            "deployment_plans",
            "personas",
            "capital_pools",
            "runtime_bindings",
            "bindings",
            "loop_runs",
            "incidents",
            "postmortems",
            "kill_switch",
            "drift_reports",
            "paper_live_drift_reports",
            "telemetry_events",
            "lineage_edges",
            "governance_audit_events",
            "approval_decisions",
            "evolution_decisions",
            "ooda_packets",
            "interventions",
            "synthesis_conflict_logs",
            "approval_queue_items",
            "governance_review_queue_items",
            "trainer_sessions",
            "persona_sessions",
            "teaching_sessions",
            "trainer_replays",
        ):
            return "typed_store"
        return "missing"

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

    def get_research_ticket(self, ticket_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_ticket(ticket_id, **kwargs)

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

    def compare_research_artifacts(self, artifact_ids: List[str]) -> Dict[str, Any]:
        return self.research_knowledge_source.compare_research_artifacts(artifact_ids)

    def compare_strategy_spec_versions(self, strategy_id: Optional[str], *, left_selector: str, right_selector: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.compare_strategy_spec_versions(strategy_id, left_selector=left_selector, right_selector=right_selector)

    def get_evidence_ref_detail(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_evidence_ref_detail(ref_id)

    def get_insight_card_detail(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_insight_card_detail(insight_id)

    def get_institutional_memory_entry(self, entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_institutional_memory_entry(entry_id)

    def get_last_governed_search_refs(self) -> Dict[str, Dict[str, Any]]:
        return self.research_knowledge_source.get_last_governed_search_refs()

    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_artifact(artifact_id)

    def get_research_search_index(self) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_search_index()

    def get_search_ops_snapshot(self, *, pipeline_run_limit: int = 50) -> Dict[str, Any]:
        return self.research_knowledge_source.get_search_ops_snapshot(pipeline_run_limit=pipeline_run_limit)

    def get_source_change_proposals(self, *, status: Optional[str] = None, proposal_type: Optional[str] = None, source_kind: Optional[str] = None) -> Dict[str, Any]:
        return self.research_knowledge_source.get_source_change_proposals(status=status, proposal_type=proposal_type, source_kind=source_kind)

    def get_source_connector_registry(self) -> Dict[str, Any]:
        return self.research_knowledge_source.get_source_connector_registry()

    def get_source_health_usage_snapshot(self) -> Dict[str, Any]:
        return self.research_knowledge_source.get_source_health_usage_snapshot()

    def get_source_ops_snapshot(self, *, crawl_run_limit: int = 50, dlq_status: Optional[str] = None, frontier_status: Optional[str] = None, audit_limit: int = 20) -> Dict[str, Any]:
        return self.research_knowledge_source.get_source_ops_snapshot(crawl_run_limit=crawl_run_limit, dlq_status=dlq_status, frontier_status=frontier_status, audit_limit=audit_limit)

    def get_strategy_spec_detail(self, strategy_id: Optional[str], *, version_selector: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_strategy_spec_detail(strategy_id, version_selector=version_selector)

    def list_institutional_memory_entries(self) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_institutional_memory_entries()

    def list_research_artifacts(self, *, artifact_type: Optional[str] = None, status: Optional[str] = None, tags: Optional[List[str]] = None, author: Optional[str] = None, date_range: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_artifacts(artifact_type=artifact_type, status=status, tags=tags, author=author, date_range=date_range)

    def list_research_search_results(self, *, query: str, match_type: str = "all", status: Optional[str] = None, date_range: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_search_results(query=query, match_type=match_type, status=status, date_range=date_range)

    def list_strategy_spec_versions(self, strategy_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_strategy_spec_versions(strategy_id)

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

    def artifact_exists(self, artifact_id: str) -> bool:
        return self.lifecycle_telemetry_governance.artifact_exists(artifact_id)

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_evolution_decision_by_id(decision_id)

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_evolution_decisions_by_incident(incident_id)

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_inspiration_graph(artifact_id)

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_lineage_edge(edge_id)

    def get_lineage_graph_nodes(self, edges: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        return self.lifecycle_telemetry_governance.get_lineage_graph_nodes(edges)

    def get_loop_run(self, loop_run_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.lifecycle_telemetry_governance.get_loop_run(loop_run_id)

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_postmortem_by_incident(incident_id)

    def get_rollback_review(self, rollback_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_rollback_review(rollback_id)

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.get_rollbacks_by_incident(incident_id)

    def get_sentinel_finding(self, finding_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.lifecycle_telemetry_governance.get_sentinel_finding(finding_id)

    def list_freeze_orders(self, status: Optional[str] = None, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_freeze_orders(status=status, scope=scope)

    def list_lineage_records(self, artifact_id: Optional[str] = None, include_fixture_pack: bool = True) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_lineage_records(artifact_id=artifact_id, include_fixture_pack=include_fixture_pack)

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_telemetry_summaries()

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
        try:
            if hasattr(self.persona_training, "get_capability_snapshot"):
                return self.persona_training.get_capability_snapshot(snapshot_id)
        except Exception:
            return None
        return None

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            if hasattr(self.persona_training, "get_capability_snapshot_for_persona"):
                return self.persona_training.get_capability_snapshot_for_persona(persona_id)
            if persona_id and hasattr(self.persona_training, "get_persona_capabilities"):
                return self.persona_training.get_persona_capabilities(persona_id)
        except Exception:
            return None
        return None

    def trade_journey_projection_reader(self) -> Any:
        override = getattr(self, "_trade_journey_projection_reader_override", None)
        if override is not None:
            return override
        if hasattr(self.lifecycle_telemetry_governance, "trade_journey_projection_reader"):
            return self.lifecycle_telemetry_governance.trade_journey_projection_reader()
        return None

    # -------------------------------------------------------------------------
    # Domain-Owned Read Projections
    # -------------------------------------------------------------------------
    def get_ranking_formula(self, formula_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not formula_id:
            return None
        if hasattr(self.persona_capital_runtime, "get_ranking_formula"):
            return self.persona_capital_runtime.get_ranking_formula(formula_id)
        for formula in self.persona_capital_runtime.list_ranking_formulas():
            if formula.get("formula_id") == formula_id or formula.get("id") == formula_id:
                return formula
        return None

    def get_ranking_snapshot(self, ranking_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ranking_id:
            return None
        return self.persona_capital_runtime.get_ranking(ranking_id)

    def get_allocation_evaluation(self, alloc_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not alloc_id:
            return None
        for item in self.persona_capital_runtime.list_capital_allocations():
            if item.get("id") == alloc_id or item.get("allocation_id") == alloc_id:
                return item
        return None

    def get_approval_decision(self, decision_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not decision_id:
            return None
        did_str = str(decision_id).strip()
        if hasattr(self.ooda_management, "review_queue") and hasattr(self.ooda_management.review_queue, "_approval_decisions_reader"):
            reader = self.ooda_management.review_queue._approval_decisions_reader
            if callable(reader):
                for item in reader() or []:
                    if str(item.get("decision_id") or item.get("id") or "").strip() == did_str:
                        return item
        for item in self.ooda_management.list_approval_queue_items():
            if str(item.get("decision_id") or item.get("id") or item.get("item_id") or "").strip() in {did_str, f"review-{did_str}"}:
                return item
        return None

    def list_approval_decisions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ooda_management.list_approval_queue_items(**kwargs)

    def get_review_summary(
        self,
        plan_id: Optional[str] = None,
        *,
        plan: Optional[Dict[str, Any]] = None,
        decision: Any = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Return review summary for a deployment plan or diagnostic surface status."""
        if plan_id is None and plan is None and decision is None:
            if hasattr(self.ooda_management, "get_surface_status"):
                return self.ooda_management.get_surface_status(**kwargs)
            return {"status": "ok"}
        if plan is None and plan_id:
            plan = self.get_deployment_plan(plan_id)
        if decision is None and plan:
            decision = self.get_approval_decision(plan.get("approval_decision_id"))
        if plan or decision:
            return ManagementReviewQueuePort.derive_review_summary_for_plan(plan or {}, decision)
        return None

    def get_route_policy_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        for policy in self.operations_consultation.list_route_policies():
            if policy.get("persona_id") == persona_id or policy.get("id") == persona_id:
                return policy
        return None

    def get_consult_policy(
        self,
        persona_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Return consult policy for a persona or first available consult rule."""
        if persona_id:
            pid_str = str(persona_id).strip()
            for rule in self.operations_consultation.list_consult_rules(**kwargs):
                if str(rule.get("persona_id") or rule.get("id") or "").strip() == pid_str:
                    return rule
            return None
        rules = self.operations_consultation.list_consult_rules(**kwargs)
        return rules[0] if rules else None

    def get_persona_consult_policy(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.get_consult_policy(persona_id)

    def get_allowed_actions(
        self,
        plan_id: Optional[str] = None,
        *,
        plan: Optional[Dict[str, Any]] = None,
        decision: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Derive allowed actions for deployment plans."""
        if plan is None and plan_id:
            plan = self.get_deployment_plan(plan_id)
        if decision is None and plan:
            decision = self.get_approval_decision(plan.get("approval_decision_id"))
        if plan:
            return ManagementReviewQueuePort.derive_allowed_actions_for_plan(plan, decision)
        return {
            "canApprove": False,
            "canReject": False,
            "canPromoteToPaper": False,
        }

    def get_persona_allowed_actions(
        self,
        persona_id: Optional[str],
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Derive allowed actions for a persona based on lifecycle state and active sessions."""
        if not persona_id:
            return None
        persona = self.get_persona(persona_id)
        if not persona:
            return None

        lifecycle_state = persona.get("lifecycle_state", "unknown")
        sessions = self.get_sessions_for_persona(persona_id) or []
        active_sessions = [s for s in sessions if s.get("status") == "active"]

        actions: Dict[str, Any] = {}

        if lifecycle_state == "draft":
            actions["canActivate"] = True
            actions["canEdit"] = True
            actions["canDelete"] = True
        elif lifecycle_state == "active":
            actions["canActivate"] = False
            actions["canEdit"] = True
            actions["canDelete"] = False
            actions["canRetire"] = True
            actions["canPause"] = len(active_sessions) == 0
        elif lifecycle_state == "retired":
            actions["canActivate"] = False
            actions["canEdit"] = False
            actions["canDelete"] = False
            actions["canRetire"] = False
            actions["canPause"] = False

        if active_sessions:
            actions["canTerminateSession"] = True
            actions["canPauseSession"] = True

        teaching_sessions = self.get_teaching_sessions_for_persona(persona_id) or []
        if teaching_sessions:
            actions["canViewTeachingHistory"] = True

        return actions

    def get_rollbacks(self, runtime_id: Optional[str] = None) -> List[Dict[str, Any]]:
        _, runs = self.lifecycle_telemetry_governance.list_loop_runs()
        if runtime_id:
            return [r for r in runs if r.get("runtime_id") == runtime_id]
        return runs

    def list_all_rollbacks(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.get_rollbacks()

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_paper_live_drift_reports()

    def list_paper_runtime_monitoring_sessions(self) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_paper_live_drift_reports()

    def get_paper_runtime_monitoring_session(
        self,
        session_id: Optional[str] = None,
        *,
        runtime_id: Optional[str] = None,
        binding_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        session_id_str = str(session_id or "").strip()
        runtime_id_str = str(runtime_id or "").strip()
        binding_id_str = str(binding_id or "").strip()
        if not session_id_str and not runtime_id_str and not binding_id_str:
            return None
        sessions = self.list_paper_runtime_monitoring_sessions()
        for session in sessions:
            if session_id_str and (
                str(session.get("session_id") or session.get("id") or "").strip() == session_id_str
            ):
                return session
            s_runtime_id = str(session.get("runtime_id") or "").strip()
            s_binding_id = str(
                session.get("binding_id") or session.get("runtime_binding_id") or ""
            ).strip()
            if binding_id_str and s_binding_id == binding_id_str:
                return session
            if runtime_id_str and s_runtime_id == runtime_id_str:
                return session
        return None

    def get_paper_live_drift_report(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        rid_str = str(runtime_id).strip()
        for r in self.lifecycle_telemetry_governance.list_paper_live_drift_reports():
            if str(r.get("runtime_id") or r.get("id") or "").strip() == rid_str:
                return r
        return None

    def get_latest_run(
        self,
        plan_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        _, runs = self.lifecycle_telemetry_governance.list_loop_runs()
        if plan_id:
            pid_str = str(plan_id).strip()
            for r in runs:
                if str(r.get("plan_id") or r.get("id") or "").strip() == pid_str:
                    return r
            return {"progress": 0.0}
        return runs[0] if runs else None

    def get_experiment_bff(self, exp_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_experiment(exp_id)

    def get_job_bff(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.research_knowledge_source.get_research_ticket(job_id)

    def list_jobs_bff(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_tickets(**kwargs)

    def list_events_bff(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.lifecycle_telemetry_governance.list_telemetry_events(**kwargs)

    def get_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            return self.persona_training.get_trainer_session(session_id)
        except Exception:
            return None

    def get_sessions_for_persona(self, persona_id: str) -> List[Dict[str, Any]]:
        try:
            return self.persona_training.list_persona_sessions(persona_id)
        except Exception:
            return []

    def list_sessions_for_persona(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            return self.persona_training.list_persona_sessions(persona_id, **kwargs)
        except Exception:
            return []

    def get_teaching_sessions_for_persona(self, persona_id: str) -> List[Dict[str, Any]]:
        try:
            return self.persona_training.list_persona_teaching_sessions(persona_id)
        except Exception:
            return []

    def list_teaching_sessions_for_persona(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            return self.persona_training.list_persona_teaching_sessions(persona_id, **kwargs)
        except Exception:
            return []

    def build_trainer_preview_unavailable(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.persona_training.get_trainer_preview(session_id, **kwargs)

    def list_decision_journal_entries(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_research_notes(**kwargs)

    def list_registry_entries(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.research_knowledge_source.list_data_sources(**kwargs)

    def list_committees(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.operations_consultation.list_committees(**kwargs)

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.operations_consultation.get_committee(committee_id or "")

    def list_committee_session_memos(
        self,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """ASK-004: list memos linked to a committee session."""
        if hasattr(self.operations_consultation, "list_committee_session_memos") and session_id:
            res = self.operations_consultation.list_committee_session_memos(str(session_id))
            if res:
                return res
        memos = self.operations_consultation.list_consult_memos(**kwargs)
        if session_id:
            sid_str = str(session_id).strip()
            return [
                m for m in memos
                if str(
                    m.get("linked_session_id")
                    or (m.get("session_to_memo_mapping") or {}).get("session_id")
                    or ""
                ).strip() == sid_str
            ]
        return memos

    def get_committee_session_memo(
        self,
        *args: Any,
        session_id: Optional[str] = None,
        memo_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """ASK-004: get a memo linked to a committee session."""
        if len(args) == 2:
            session_id, memo_id = args[0], args[1]
        elif len(args) == 1:
            if session_id is None:
                memo_id = args[0]
            else:
                memo_id = args[0]
        if not memo_id:
            return None
        if hasattr(self.operations_consultation, "get_committee_session_memo") and session_id:
            res = self.operations_consultation.get_committee_session_memo(str(session_id), str(memo_id))
            if res is not None:
                return res
        memo = self.operations_consultation.get_consult_memo(str(memo_id))
        if memo is None:
            return None
        if session_id:
            linked_session = str(
                memo.get("linked_session_id")
                or (memo.get("session_to_memo_mapping") or {}).get("session_id")
                or ""
            ).strip()
            if linked_session and linked_session != str(session_id).strip():
                return None
        return memo

    def list_agora_insights(self, **kwargs: Any) -> List[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "list_agora_insights"):
            res = self.operations_consultation.list_agora_insights(**kwargs)
            if res:
                return res
        return self.research_knowledge_source.list_insight_cards(**kwargs)

    def list_agora_notes(self, **kwargs: Any) -> List[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "list_agora_notes"):
            res = self.operations_consultation.list_agora_notes(**kwargs)
            if res:
                return res
        return self.research_knowledge_source.list_research_notes(**kwargs)

    def list_agora_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "list_agora_sessions"):
            res = self.operations_consultation.list_agora_sessions(**kwargs)
            if res:
                return res
        return self.operations_consultation.list_consult_requests(**kwargs)

    def list_agora_signals(self, **kwargs: Any) -> List[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "list_agora_signals"):
            res = self.operations_consultation.list_agora_signals(**kwargs)
            if res:
                return res
        return self.research_knowledge_source.list_evidence_refs(**kwargs)

    def list_agora_training_examples(self, **kwargs: Any) -> List[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "list_agora_training_examples"):
            res = self.operations_consultation.list_agora_training_examples(**kwargs)
            if res:
                return res
        return self.persona_training.list_trainer_replays(**kwargs)

    def list_agora_watchlist(self, **kwargs: Any) -> List[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "list_agora_watchlist"):
            res = self.operations_consultation.list_agora_watchlist(**kwargs)
            if res:
                return res
        return self.persona_capital_runtime.list_personas(**kwargs)

    def get_agora_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "get_agora_session"):
            res = self.operations_consultation.get_agora_session(session_id or "")
            if res is not None:
                return res
        return self.operations_consultation.get_consult_request(session_id or "")

    def get_agora_signal(self, signal_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if hasattr(self.operations_consultation, "get_agora_signal"):
            res = self.operations_consultation.get_agora_signal(signal_id or "")
            if res is not None:
                return res
        return self.research_knowledge_source.get_evidence_ref(signal_id or "")

    def get_agora_committee_evidence_pack(self, session_id: Optional[str]) -> Any:
        if hasattr(self.operations_consultation, "get_agora_committee_evidence_pack"):
            res = self.operations_consultation.get_agora_committee_evidence_pack(session_id or "")
            if res is not None:
                return res
        return self.operations_consultation.get_consultation_evidence(session_id or "")


def create_read_surface_ports(
    *,
    operations_consultation: Optional[OperationsConsultationPort] = None,
    persona_capital_runtime: Optional[Union[CompositePersonaCapitalRuntimePort, PersonaCapitalRuntimeDomainPort]] = None,
    persona_registry_store: Optional[Any] = None,
    ooda_management: Optional[OodaManagementDomainPort] = None,
    research_knowledge_source: Optional[ResearchKnowledgeSourcePort] = None,
    lifecycle_telemetry_governance: Optional[CompositeLifecycleTelemetryGovernancePort] = None,
    persona_training: Optional[PersonaTrainingDomainPort] = None,
    **kwargs: Any,
) -> ReadSurfacePorts:
    """Factory creating a production-grade composite ReadSurfacePorts instance."""
    if persona_registry_store is not None:
        if persona_capital_runtime is None:
            persona_capital_runtime = PersonaCapitalRuntimeDomainPort(
                persona_port=PersonaFleetPort(store=persona_registry_store),
            )
        if persona_training is None:
            persona_training = PersonaTrainingDomainPort(
                persona_port=PersonaRegistryReadsPort(store=persona_registry_store),
            )
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
    ooda_p = ooda_kw.get("ooda_port") or OodaPacketsPort(records_provider=lambda: list(ooda_kw.get("ooda_packets") or []))
    int_p = ooda_kw.get("interventions_port") or InterventionsPort(records_provider=lambda: list(ooda_kw.get("interventions") or []))
    scl_p = ooda_kw.get("synthesis_conflict_logs_port") or SynthesisConflictLogsPort(records_provider=lambda: list(ooda_kw.get("synthesis_conflict_logs") or []))
    rq_p = ooda_kw.get("review_queue_port") or ManagementReviewQueuePort(
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
