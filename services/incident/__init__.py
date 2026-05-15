"""
Pantheon Incident Domain — INC-001

Provides IncidentCase and Postmortem backbone objects that attach to
runtime binding, deployment stage, and lineage evidence refs.

Write authority
---------------
Incident domain only. Objects are created when a runtime anomaly or
governance trigger requires formal incident tracking.

Lineage edges (formal, normalized)
-----------------------------------
incident_case.runtime_binding  : IncidentCase.binding_id → RuntimeBinding
postmortem.incident_case       : Postmortem.incident_id  → IncidentCase
"""

from .incident import (
    IncidentCase,
    IncidentSeverity,
    IncidentStatus,
    IncidentStore,
    Postmortem,
    PostmortemStatus,
    validate_incident_case,
    validate_postmortem,
)
from .evidence_collector import (
    EvidenceBundle,
    PostmortemEvidenceCollector,
    RuntimeBindingEvidence,
    TelemetryEvidence,
    build_evidence_bundle,
)

__all__ = [
    "EvidenceBundle",
    "IncidentCase",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentStore",
    "PostmortemEvidenceCollector",
    "Postmortem",
    "PostmortemStatus",
    "RuntimeBindingEvidence",
    "TelemetryEvidence",
    "build_evidence_bundle",
    "validate_incident_case",
    "validate_postmortem",
]
