"""Control-room incident surface must preserve incident-owner availability."""

from services.control_plane.bff.control_loops.service import ControlLoopsService
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
    CompositeLifecycleTelemetryGovernancePort,
    DomainIncidentPort,
    DomainLifecyclePort,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts


def _control_room(opener):
    incident_port = DomainIncidentPort(
        incidents_api_url="http://incident-owner.invalid", opener=opener
    )
    ports = ReadSurfacePorts(
        lifecycle_telemetry_governance=CompositeLifecycleTelemetryGovernancePort(
            incident_port=incident_port,
            lifecycle_port=DomainLifecyclePort(loop_runs={}),
        )
    )
    return ControlLoopsService(read_store=ports).control_room()["incidents"]


def test_incident_owner_outage_is_unavailable_not_ok() -> None:
    def down(*_args, **_kwargs):
        raise OSError("incident owner unavailable")

    result = _control_room(down)
    assert result["items"] == []
    assert result["meta"]["surfaces"]["incidents"]["status"] == "unavailable"


def test_missing_incident_owner_is_unavailable() -> None:
    ports = ReadSurfacePorts(
        lifecycle_telemetry_governance=CompositeLifecycleTelemetryGovernancePort(
            incident_port=DomainIncidentPort(incidents_api_url=""),
            lifecycle_port=DomainLifecyclePort(loop_runs={}),
        )
    )
    result = ControlLoopsService(read_store=ports).control_room()["incidents"]
    assert result["meta"]["surfaces"]["incidents"]["status"] == "unavailable"


def test_healthy_empty_incident_owner_is_ok() -> None:
    ports = ReadSurfacePorts(
        lifecycle_telemetry_governance=CompositeLifecycleTelemetryGovernancePort(
            incident_port=DomainIncidentPort(incidents={}),
            lifecycle_port=DomainLifecyclePort(loop_runs={}),
        )
    )
    result = ControlLoopsService(read_store=ports).control_room()["incidents"]
    assert result["items"] == []
    assert result["meta"]["surfaces"]["incidents"]["status"] == "ok"
