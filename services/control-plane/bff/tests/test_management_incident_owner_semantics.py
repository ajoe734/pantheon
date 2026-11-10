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


def test_investigating_only_tenant_is_degraded_and_counted_open() -> None:
    svc = _service(
        DomainIncidentPort(
            incidents={"inc-1": {"incident_id": "inc-1", "status": "investigating", "severity": "high"}}
        )
    )
    health = svc.get_operator_health_status()
    group = next(
        g for g in health.get("data", health)["groups"] if g["group_id"] == "incident"
    )
    assert group["status"] == "degraded"
    assert group["details"]["open_incidents"] == 1
    counts = svc.build_shell_summary_counts()
    assert counts["counts"]["open_alerts"] == 1, counts


def _open_only_all(requested) -> bool:
    return bool(requested) and all(q.get("open_only") == ["true"] for q in requested)


def test_shell_summary_health_and_alerts_request_open_only_from_owner() -> None:
    port, requested = _capturing_port()
    svc = ManagementService(read_store=_ports(port))

    counts = svc.build_shell_summary_counts()
    assert counts["counts"]["open_alerts"] == 2, counts
    assert _open_only_all(requested)

    requested.clear()
    health = svc.get_operator_health_status()
    group = next(g for g in health.get("data", health)["groups"] if g["group_id"] == "incident")
    assert group["details"] == {"open_incidents": 2}
    assert _open_only_all(requested)

    requested.clear()
    alerts = svc.get_operator_alerts()
    incident_ids = {a["alert_id"] for a in alerts["alerts"] if a["category"] == "incident"}
    assert incident_ids == {"inc-open", "inc-inv"}
    assert alerts["meta"]["surfaces"]["incident_feed"]["status"] == "ok"
    assert _open_only_all(requested)


def test_failed_incident_owner_is_unavailable_not_zero_for_open_only_reads() -> None:
    svc = _service(DomainIncidentPort(incidents_api_url="http://incident-owner.invalid", opener=_down))
    health = svc.get_operator_health_status()
    group = next(g for g in health.get("data", health)["groups"] if g["group_id"] == "incident")
    assert group["details"]["open_incidents"] is None
    assert "total_incidents" not in group["details"]
    assert svc.get_operator_alerts()["meta"]["surfaces"]["incident_feed"]["status"] == "unavailable"
