"""BFF Domain Ports package.

This package provides typed domain ports for all BFF read domains:
- operations_consultation: Workflows, hooks, OpenClaw ops, and consultation
- persona_capital_runtime: Persona fleet, capital pools, deployments, runtimes, rankings, evolution
- ooda_management: OODA loop packets, interventions, conflict resolution logs, review queues
- research_knowledge_source: Research, knowledge workbench, institutional memory, search, data sources
- lifecycle_telemetry_governance: Lifecycle, telemetry, incidents, governance, lineage
- persona_training: Persona profiles, trainer sessions, replays, rapid evaluation
- read_surface_ports: Unified ReadSurfacePorts container and factories

All ports in this package resolve domain reads directly against typed domain
stores and service clients without importing or delegating to ReadSurfaceStore.
"""
from __future__ import annotations

try:
    from services.control_plane.bff.ports.operations_consultation import (
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
        OpenClawOpsClient,
        OpenClawOpsClientError,
    )
    from services.control_plane.bff.ports.persona_capital_runtime import (
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
        PERSONA_OPERATIONAL_LIFECYCLE_STATES,
    )
    from services.control_plane.bff.ports.ooda_management import (
        OodaPacketsPort,
        InterventionsPort,
        SynthesisConflictLogsPort,
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
    )
    from services.control_plane.bff.ports.research_knowledge_source import (
        ResearchKnowledgeSourcePort,
        DefaultResearchKnowledgeSourcePort,
    )
    from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
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
    )
    from services.control_plane.bff.ports.persona_training import (
        PersonaRegistryReadsPort,
        TrainingSessionTrainerPort,
        RapidEvaluationPort,
        RapidEvaluationOwnership,
        PersonaTrainingDomainPort,
    )
    from services.control_plane.bff.ports.read_surface_ports import (
        ReadSurfacePorts,
        create_read_surface_ports,
        create_in_memory_read_surface_ports,
    )
    from services.control_plane.bff.ports.persona_write_owner import (
        PersonaRegistryHttpWritePort,
        PersonaWriteConflict,
        PersonaWriteOwnerUnavailable,
        create_persona_registry_write_owner,
    )
    from services.control_plane.bff.ports.rankings import (
        RankingSnapshotWriteOwnerPort,
        create_ranking_write_owner,
    )
except ImportError:
    from services.control_plane.bff.ports.operations_consultation import (  # type: ignore[no-redef]
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
        OpenClawOpsClient,
        OpenClawOpsClientError,
    )
    from services.control_plane.bff.ports.persona_capital_runtime import (  # type: ignore[no-redef]
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
        PERSONA_OPERATIONAL_LIFECYCLE_STATES,
    )
    from services.control_plane.bff.ports.ooda_management import (  # type: ignore[no-redef]
        OodaPacketsPort,
        InterventionsPort,
        SynthesisConflictLogsPort,
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
    )
    from services.control_plane.bff.ports.research_knowledge_source import (  # type: ignore[no-redef]
        ResearchKnowledgeSourcePort,
        DefaultResearchKnowledgeSourcePort,
    )
    from services.control_plane.bff.ports.lifecycle_telemetry_governance import (  # type: ignore[no-redef]
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
    )
    from services.control_plane.bff.ports.persona_training import (  # type: ignore[no-redef]
        PersonaRegistryReadsPort,
        TrainingSessionTrainerPort,
        RapidEvaluationPort,
        RapidEvaluationOwnership,
        PersonaTrainingDomainPort,
    )
    from services.control_plane.bff.ports.read_surface_ports import (  # type: ignore[no-redef]
        ReadSurfacePorts,
        create_read_surface_ports,
        create_in_memory_read_surface_ports,
    )
    from services.control_plane.bff.ports.persona_write_owner import (  # type: ignore[no-redef]
        PersonaRegistryHttpWritePort,
        PersonaWriteConflict,
        PersonaWriteOwnerUnavailable,
        create_persona_registry_write_owner,
    )
    from services.control_plane.bff.ports.rankings import (  # type: ignore[no-redef]
        RankingSnapshotWriteOwnerPort,
        create_ranking_write_owner,
    )

__all__ = [
    # Operations & Consultation
    "WorkflowHookCatalogReaderPort",
    "DomainWorkflowCatalogPort",
    "OpenClawOperationsReaderPort",
    "DomainOpenClawOperationsPort",
    "ConsultationReaderPort",
    "DomainConsultationPort",
    "OperationsConsultationPort",
    "CompositeOperationsConsultationPort",
    "InMemoryOperationsConsultationPort",
    "create_operations_consultation_port",
    "create_in_memory_operations_consultation_port",
    "OpenClawOpsClient",
    "OpenClawOpsClientError",
    # Persona & Capital & Runtime
    "PersonaFleetPort",
    "CapitalPoolPort",
    "DeploymentPlanPort",
    "RuntimePort",
    "RankingProjectionPort",
    "EvolutionProjectionPort",
    "PersonaCapitalRuntimeDomainPort",
    "CompositePersonaCapitalRuntimePort",
    "InMemoryPersonaCapitalRuntimePort",
    "create_persona_capital_runtime_port",
    "create_in_memory_persona_capital_runtime_port",
    "PERSONA_OPERATIONAL_LIFECYCLE_STATES",
    # OODA & Management
    "OodaPacketsPort",
    "InterventionsPort",
    "SynthesisConflictLogsPort",
    "ManagementReviewQueuePort",
    "OodaManagementDomainPort",
    # Research, Knowledge & Source
    "ResearchKnowledgeSourcePort",
    "DefaultResearchKnowledgeSourcePort",
    # Lifecycle, Telemetry & Governance
    "IncidentReaderPort",
    "DomainIncidentPort",
    "LifecycleReaderPort",
    "DomainLifecyclePort",
    "GovernanceReaderPort",
    "DomainGovernancePort",
    "LineageReaderPort",
    "DomainLineagePort",
    "TelemetryReaderPort",
    "DomainTelemetryPort",
    "CompositeLifecycleTelemetryGovernancePort",
    "InMemoryLifecycleTelemetryGovernancePort",
    "create_lifecycle_telemetry_governance_port",
    "create_in_memory_lifecycle_telemetry_governance_port",
    # Persona Training
    "PersonaRegistryReadsPort",
    "TrainingSessionTrainerPort",
    "RapidEvaluationPort",
    "RapidEvaluationOwnership",
    "PersonaTrainingDomainPort",
    # Unified Read Surface Ports
    "ReadSurfacePorts",
    "create_read_surface_ports",
    "create_in_memory_read_surface_ports",
    # Persona write owner (deliberately outside ReadSurfacePorts)
    "PersonaRegistryHttpWritePort",
    "PersonaWriteConflict",
    "PersonaWriteOwnerUnavailable",
    "create_persona_registry_write_owner",
    # Ranking snapshot write owner (deliberately outside ReadSurfacePorts)
    "RankingSnapshotWriteOwnerPort",
    "create_ranking_write_owner",
]
