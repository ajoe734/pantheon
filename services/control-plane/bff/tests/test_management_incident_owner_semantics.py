"""Management surfaces must keep investigating incidents open and expose owner outages."""

from services.control_plane.bff.governance.human_inbox import _human_inbox_incident_item
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
    CompositeLifecycleTelemetryGovernancePort,
    DomainIncidentPort,
    DomainLifecyclePort,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts


def _service(incident_port) -> ManagementService:
    ports = ReadSurfacePorts(
        lifecycle_telemetry_governance=CompositeLifecycleTelemetryGovernancePort(
            incident_port=incident_port,
            lifecycle_port=DomainLifecyclePort(loop_runs={}),
        )
    )
    return ManagementService(read_store=ports)


def _down(*_a, **_k):
    raise OSError("incident owner unavailable")


def test_investigating_incident_stays_open_in_inbox_and_projection() -> None:
    record = {"incident_id": "inc-1", "status": "investigating", "severity": "high"}
    assert _human_inbox_incident_item(record) is not None
    svc = _service(DomainIncidentPort(incidents={"inc-1": record}))
    items = [
        i for i in svc.get_human_inbox()["data"]["items"] if i.get("source_type") == "incident"
    ]
    assert items and all(i["action_state"] == "pending" for i in items)


def test_anomalies_incident_surface_reflects_owner_availability() -> None:
    out = _service(DomainIncidentPort(
        incidents_api_url="http://incident-owner.invalid", opener=_down
    )).get_management_anomalies()
    assert out["meta"]["surfaces"]["incidents"]["status"] == "unavailable"
    missing = _service(DomainIncidentPort(incidents_api_url="")).get_management_anomalies()
    assert missing["meta"]["surfaces"]["incidents"]["status"] == "unavailable"
    healthy = _service(DomainIncidentPort(incidents={})).get_management_anomalies()
    assert healthy["meta"]["surfaces"]["incidents"]["status"] == "ok"
