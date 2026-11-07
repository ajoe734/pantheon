"""Composition test: main anomalies builder requests open_only from the incident owner."""

from services.control_plane.bff.ports.lifecycle_telemetry_governance import DomainIncidentPort
from services.control_plane.bff.tests.test_management_incident_owner_semantics import (
    _capturing_port,
    _down,
    _ports,
)


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
