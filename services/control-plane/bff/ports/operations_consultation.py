"""Operations, OpenClaw, and Consultation narrow domain ports.

Re-exports typed domain ports, protocols, and factory functions for Operations,
Workflow catalog, OpenClaw, and Consultation reads.
"""
from __future__ import annotations

try:
    from domain_ports.operations_consultation import (
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
except ImportError:
    from services.control_plane.bff.domain_ports.operations_consultation import (  # type: ignore[no-redef]
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

__all__ = [
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
]
