"""OODA loop packets, interventions, conflict resolution logs, and review queues domain ports.

Re-exports typed domain ports, protocols, and factory functions for OODA,
Interventions, Synthesis conflict logs, and Management review queues.
"""
from __future__ import annotations

try:
    from domain_ports.ooda_management import (
        OodaPacketsPort,
        InterventionsPort,
        SynthesisConflictLogsPort,
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
    )
except ImportError:
    from services.control_plane.bff.domain_ports.ooda_management import (  # type: ignore[no-redef]
        OodaPacketsPort,
        InterventionsPort,
        SynthesisConflictLogsPort,
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
    )

__all__ = [
    "OodaPacketsPort",
    "InterventionsPort",
    "SynthesisConflictLogsPort",
    "ManagementReviewQueuePort",
    "OodaManagementDomainPort",
]
