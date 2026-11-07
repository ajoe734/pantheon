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


def _owner_rows():
    return [
        {"incident_id": "inc-open", "status": "open", "severity": "high"},
        {"incident_id": "inc-inv", "status": "investigating", "severity": "medium"},
        {"incident_id": "inc-res", "status": "resolved", "severity": "low"},
        {"incident_id": "inc-closed", "status": "closed", "severity": "low"},
    ]


def _capturing_port():
    import io
    import json as _json
    from urllib.parse import parse_qs, urlparse

    requested: list[dict] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def opener(req, timeout=None):
        query = parse_qs(urlparse(req.full_url).query)
        requested.append(query)
        rows = _owner_rows()
        if query.get("open_only") == ["true"]:
            rows = [r for r in rows if r["status"] in ("open", "investigating")]
        return _Resp(_json.dumps(rows).encode("utf-8"))

    port = DomainIncidentPort(incidents_api_url="http://incident-owner.invalid", opener=opener)
    return port, requested


def _ports(port) -> ReadSurfacePorts:
    return ReadSurfacePorts(
        lifecycle_telemetry_governance=CompositeLifecycleTelemetryGovernancePort(
            incident_port=port, lifecycle_port=DomainLifecyclePort(loop_runs={})
        )
    )


def test_management_service_anomalies_request_open_only_from_owner() -> None:
    port, requested = _capturing_port()
    out = ManagementService(read_store=_ports(port)).get_management_anomalies()
    assert requested and all(q.get("open_only") == ["true"] for q in requested)
    ids = {i["id"] for i in out["items"] if i["kind"] == "incident"}
    assert ids == {"inc-open", "inc-inv"}
    assert out["meta"]["surfaces"]["incidents"]["status"] == "ok"


def test_main_anomalies_builder_requests_open_only_from_owner(monkeypatch) -> None:
    from services.control_plane.bff import main as bff_main

    port, requested = _capturing_port()
    monkeypatch.setattr(bff_main, "read_store", _ports(port))
    out = bff_main._build_management_anomalies_payload("2026-11-07T00:00:00Z")
    assert requested and all(q.get("open_only") == ["true"] for q in requested)
    ids = {i["id"] for i in out["items"] if i["kind"] == "incident"}
    assert ids == {"inc-open", "inc-inv"}


def test_main_anomalies_builder_marks_failed_owner_unavailable(monkeypatch) -> None:
    from services.control_plane.bff import main as bff_main

    port = DomainIncidentPort(incidents_api_url="http://incident-owner.invalid", opener=_down)
    monkeypatch.setattr(bff_main, "read_store", _ports(port))
    out = bff_main._build_management_anomalies_payload("2026-11-07T00:00:00Z")
    assert out["meta"]["surfaces"]["incidents"]["status"] == "unavailable"
    assert not [i for i in out["items"] if i["kind"] == "incident"]
