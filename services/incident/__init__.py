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

__all__ = [
    "IncidentCase",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentStore",
    "Postmortem",
    "PostmortemStatus",
    "validate_incident_case",
    "validate_postmortem",
]
