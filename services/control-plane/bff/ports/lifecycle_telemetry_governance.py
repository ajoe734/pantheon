"""Lifecycle, Telemetry, Incident, Governance, and Lineage narrow domain ports.

Re-exports typed domain ports, protocols, and factory functions for Lifecycle,
Telemetry, Incidents/Postmortems, Governance/Audit, and Lineage reads.
"""
from __future__ import annotations

try:
    from domain_ports.lifecycle_telemetry_governance import (
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
except ImportError:
    from services.control_plane.bff.domain_ports.lifecycle_telemetry_governance import (  # type: ignore[no-redef]
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

__all__ = [
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
]
