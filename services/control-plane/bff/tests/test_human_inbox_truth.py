"""BFF-HUMAN-INBOX-TRUTH-20261104: the human inbox reports what the owners hold.

Every test drives the real composed app (``bff_main.app``) through
``/bff/management/human-inbox`` with real read ports. The incidents owner is a
real local HTTP server serving rows in the shape the hosted owner returns; the
approval and governance-review ports are the real ``ManagementReviewQueuePort``
fed with the hosted decision shape.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models import router as inbox_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.personas.routes.common import run_management_read
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
    DomainIncidentPort,
    create_lifecycle_telemetry_governance_port,
)
from services.control_plane.bff.ports.ooda_management import (
    ManagementReviewQueuePort,
    OodaManagementDomainPort,
)

HEADERS = {"Authorization": "Bearer op-mgmt-cap-001:operator,admin:mfa"}

INCIDENT_KEYS = (
    "artifact_id artifact_version binding_id capital_pool_id created_at deployment_plan_id "
    "deployment_stage evidence_summary incident_cluster_id incident_id lineage_ref "
    "persona_capital_binding_id reconciliation_ids resolved_at runtime_id severity status "
    "telemetry_event_ids title trace_id"
).split()


def _incident(index: int, status: str, severity: str = "critical") -> Dict[str, Any]:
    row: Dict[str, Any] = {key: None for key in INCIDENT_KEYS}
    row.update(
        incident_id=f"inc-deployment-comp-deployment-saga-plan-persona-paper-{index:04d}",
        title=f"Deployment compensation entered safe mode for deployment-saga-plan-persona-paper-{index:04d}",
        status=status,
        severity=severity,
        evidence_summary=f"saga compensation {index} entered safe mode",
        created_at=f"2026-10-{(index % 28) + 1:02d}T00:00:00Z",
        resolved_at=None if status in ("open", "investigating") else "2026-11-01T00:00:00Z",
        reconciliation_ids=[],
        telemetry_event_ids=[],
    )
    return row


def _hosted_incidents(open_count: int = 7, resolved: int = 2583, closed: int = 2) -> List[Dict[str, Any]]:
    rows = [_incident(i, "open") for i in range(open_count)]
    rows += [_incident(1000 + i, "resolved", "high") for i in range(resolved)]
    rows += [_incident(5000 + i, "closed", "low") for i in range(closed)]
    return rows


def _approval(index: int, target_type: str = "registry_entry") -> Dict[str, Any]:
    return {
        "decision_id": f"apv-{index}",
        "decision_state": "proposed",
        "target_type": target_type,
        "target_id": f"target-{index}",
        "risk_level": "medium",
        "created_at": "2026-11-03T08:00:00Z",
        "actor_id": "persona-agent",
        "rationale": f"promote target-{index}",
        "metadata": {},
    }


def _evolution_decision(index: int) -> Dict[str, Any]:
    return {
        "decision_id": f"evo-{index}",
        "status": "proposed",
        "risk_level": "low",
        "created_at": "2026-10-21T00:00:00Z",
        "created_by_id": "evolution-daily-sweep",
        "rationale": "flag_for_review",
    }


class IncidentOwner:
    """Real local HTTP incidents owner; records every request path."""

    def __init__(self, rows: List[Dict[str, Any]], delay: float = 0.0) -> None:
        self.rows = rows
        self.delay = delay
        self.paths: List[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner.paths.append(self.path)
                if owner.delay:
                    time.sleep(owner.delay)
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                rows = owner.rows
                if query.get("open_only") == ["true"]:
                    rows = [r for r in rows if r["status"] in ("open", "investigating")]
                body = json.dumps(rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "IncidentOwner":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


def _closed_port_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


class StalledSocket:
    """A real listening socket that accepts connections and never answers."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/stall"
        self.conns: List[socket.socket] = []
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
                self.conns.append(conn)
            except OSError:
                continue

    def close(self) -> None:
        self._stop = True
        for conn in self.conns:
            conn.close()
        self.sock.close()


@contextmanager
def _inbox_app(
    tmp_path,
    *,
    incidents_url: str,
    approvals: Any = lambda: [],
    plans: Any = lambda: [],
    evolution: Any = lambda: [],
    personas: Any = lambda **_k: [],
    service_cls: type = ManagementService,
) -> Iterator[Tuple[TestClient, Any]]:
    """The real management router + service over real read ports."""
    store = create_read_surface_ports(
        lifecycle_telemetry_governance=create_lifecycle_telemetry_governance_port(
            incident_port=DomainIncidentPort(incidents_api_url=incidents_url),
        ),
        ooda_management=OodaManagementDomainPort(
            review_queue_port=ManagementReviewQueuePort(
                deployment_plans_reader=plans,
                evolution_decisions_reader=evolution,
                approval_decisions_reader=approvals,
            )
        ),
    )
    store.list_personas = personas  # type: ignore[method-assign]
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(
        create_management_router(
            get_read_store=lambda: store,
            service=service_cls(get_read_store=lambda: store),
            run_management_read=run_management_read,
        )
    )
    yield TestClient(app), store


def _get(client: TestClient, **params: Any) -> Dict[str, Any]:
    response = client.get("/bff/management/human-inbox", headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _hosted_inbox(tmp_path, owner: IncidentOwner, **kwargs: Any):
    return _inbox_app(
        tmp_path,
        incidents_url=owner.url,
        approvals=lambda: [_approval(i) for i in range(3)],
        evolution=lambda: [_evolution_decision(i) for i in range(40)],
        **kwargs,
    )


def test_open_incidents_and_proposed_approvals_are_listed_with_one_filtered_owner_read(tmp_path) -> None:
    """Acceptance 1, 2, 5, 12: 2600 real-shape incidents, 7 open."""
    with IncidentOwner(_hosted_incidents()) as owner, _hosted_inbox(tmp_path, owner) as (client, _store):
        started = time.monotonic()
        body = _get(client)
        elapsed = time.monotonic() - started
        body_all = _get(client, page_size=200)

    # Every request hits the owner with open_only and never unfiltered.
    assert owner.paths and set(owner.paths) == {"/api/incidents?open_only=true"}
    assert len(owner.paths) == 2  # one read per inbox request
    assert elapsed < 2.5

    items = body_all["data"]["items"]
    incidents = [i for i in items if i["source_type"] == "incident"]
    approvals = [i for i in items if i["source_type"] == "approval"]
    assert len(incidents) == 7
    assert len(approvals) == 3
    assert all(i["status"] == "open" for i in incidents)
    assert not [i for i in items if i["source_type"] == "governance_review"]
    summary = body["data"]["summary"]
    assert summary["incident_count"] == 7 and summary["approval_count"] == 3
    assert summary["total_items"] == 10
    assert body["meta"]["surfaces"]["human_inbox"]["status"] == "ok"
    assert not body["meta"].get("partial")

    incident = incidents[0]
    assert incident["title"].startswith("Deployment compensation entered safe mode")
    assert incident["summary"].startswith("saga compensation")
    assert incident["priority"] == "critical"
    assert incident["action_state"] == "pending"
    assert not incident["allowedActions"].get("canApprove")
    assert not incident["allowedActions"].get("canReject")
    for key in ("item_id", "incident_id", "details"):
        assert incident[key]
    assert incident["created_at"].startswith("2026-10-")

    for approval in approvals:
        assert approval["action_state"] == "pending"
        assert approval["status"] == "proposed"
        assert approval["decision_id"] and approval["item_id"] == approval["decision_id"]
        assert approval["created_at"] == "2026-11-03T08:00:00Z"


def test_resolved_and_closed_incidents_never_enter_items_or_totals(tmp_path) -> None:
    with IncidentOwner(_hosted_incidents()) as owner, _hosted_inbox(tmp_path, owner) as (client, _s):
        everything = _get(client, page_size=200)
        resolved_only = _get(client, status="resolved")
    ids = {i["incident_id"] for i in everything["data"]["items"] if i["source_type"] == "incident"}
    assert len(ids) == 7
    assert all("-000" in i for i in ids)
    assert resolved_only["data"]["summary"]["incident_count"] == 0


def test_evolution_sweep_rows_are_hidden_until_decidable_and_approvals_listed_once(tmp_path) -> None:
    def evolution() -> List[Dict[str, Any]]:
        decidable = _evolution_decision(900)
        decidable["status"] = "reviewed"
        return [_evolution_decision(i) for i in range(40)] + [decidable]

    with IncidentOwner(_hosted_incidents()) as owner, _inbox_app(
        tmp_path,
        incidents_url=owner.url,
        approvals=lambda: [_approval(i) for i in range(3)],
        evolution=evolution,
    ) as (client, _s):
        body = _get(client, page_size=200)
    reviews = [i for i in body["data"]["items"] if i["source_type"] == "governance_review"]
    assert [r["item_id"] for r in reviews] == ["review-evo-900"]  # decidable one only
    assert reviews[0]["created_at"] == "2026-10-21T00:00:00Z"
    decision_ids = [i["decision_id"] for i in body["data"]["items"] if i["source_type"] == "approval"]
    assert sorted(decision_ids) == ["apv-0", "apv-1", "apv-2"]  # not repeated as review-apv-*
    assert not [i for i in body["data"]["items"] if str(i.get("item_id", "")).startswith("review-apv")]


def test_a_review_without_a_real_time_never_borrows_the_request_time(tmp_path) -> None:
    def plans() -> List[Dict[str, Any]]:
        return [{"plan_id": "plan-1", "status": "pending_review"}]

    with IncidentOwner([]) as owner, _inbox_app(tmp_path, incidents_url=owner.url, plans=plans) as (client, _s):
        body = _get(client)
    review = next(i for i in body["data"]["items"] if i["source_type"] == "governance_review")
    assert review["created_at"] is None
    assert review["summary"] and review["summary"] != "Policy review required"


def test_a_stalled_contributor_degrades_alone_and_the_rest_are_returned(tmp_path) -> None:
    """Acceptance 6: stalled on a real socket."""
    stall = StalledSocket()

    def stalled_approvals() -> List[Dict[str, Any]]:
        urllib.request.urlopen(stall.url, timeout=4)  # blocks on the real socket
        return []

    try:
        with IncidentOwner(_hosted_incidents()) as owner, _inbox_app(
            tmp_path, incidents_url=owner.url, approvals=stalled_approvals,
        ) as (client, _s):
            started = time.monotonic()
            body = _get(client)
            elapsed = time.monotonic() - started
    finally:
        stall.close()
    route_budget = inbox_router._get_human_inbox_read_timeout()
    assert elapsed < route_budget
    assert body["meta"]["partial"] is True
    surfaces = body["meta"]["surfaces"]
    assert surfaces["approval_queue"]["status"] == "degraded"
    assert surfaces["approval_queue"]["reason"] == "read_timeout"
    assert surfaces["incidents"]["status"] == "ok"
    assert body["data"]["summary"]["incident_count"] == 7


def test_zero_rows_with_a_successful_read_is_a_healthy_empty_inbox(tmp_path) -> None:
    with IncidentOwner([]) as owner, _inbox_app(tmp_path, incidents_url=owner.url) as (client, _s):
        body = _get(client)
    assert body["data"]["items"] == []
    surfaces = body["meta"]["surfaces"]
    for key in ("incidents", "approval_queue", "governance_review_queue", "persona_readiness", "human_inbox"):
        assert surfaces[key]["status"] == "ok", (key, surfaces[key])
    assert not body["meta"].get("partial")


@pytest.mark.parametrize("failing", ["incidents", "approval_queue", "governance_review_queue", "persona_readiness"])
def test_a_failed_owner_read_is_never_a_healthy_empty_queue(tmp_path, failing) -> None:
    """Acceptance 9: a real connection failure through the real port."""
    dead = _closed_port_url()

    def dead_http() -> List[Dict[str, Any]]:
        urllib.request.urlopen(dead + "/api/decisions", timeout=2)
        return []

    def dead_personas(**_kwargs: Any) -> List[Dict[str, Any]]:
        urllib.request.urlopen(dead + "/api/personas", timeout=2)
        return []

    with IncidentOwner([]) as owner:
        kwargs: Dict[str, Any] = {"incidents_url": owner.url}
        if failing == "incidents":
            kwargs["incidents_url"] = dead
        elif failing == "approval_queue":
            kwargs["approvals"] = dead_http
        elif failing == "governance_review_queue":
            kwargs["plans"] = dead_http
        else:
            kwargs["personas"] = dead_personas
        with _inbox_app(tmp_path, **kwargs) as (client, _s):
            body = _get(client)
    surface = body["meta"]["surfaces"][failing]
    assert surface["status"] in ("degraded", "unavailable")
    assert surface["reason"] == "contributor_read_error"
    assert body["meta"]["surfaces"]["human_inbox"]["status"] != "ok"
    assert body["meta"]["partial"] is True


def test_other_incident_port_callers_keep_swallowing_failures() -> None:
    port = DomainIncidentPort(incidents_api_url=_closed_port_url())
    assert port.list_incidents() == []
    assert port.list_incidents(status="open") == []
    with pytest.raises(Exception):
        port.list_incidents(open_only=True, raise_on_error=True)


def test_route_maps_exception_types_to_reasons_and_logs_a_warning(tmp_path, caplog) -> None:
    from services.control_plane.bff.personas.routes.common import ManagementReadSaturated, ManagementReadTimeout

    cases = [
        (ManagementReadTimeout(), "read_timeout", "timeout_fallback"),
        (ManagementReadSaturated(), "read_capacity_saturated", "capacity_fallback"),
        (RuntimeError("boom"), "read_error", "error_fallback"),
    ]
    for exc, reason, source in cases:
        class Raising(ManagementService):
            def get_human_inbox(self, *args: Any, **kwargs: Any) -> Any:
                raise exc

        with IncidentOwner([]) as owner, _inbox_app(tmp_path, incidents_url=owner.url, service_cls=Raising) as (client, _s):
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                body = _get(client)
        surface = body["meta"]["surfaces"]["human_inbox"]
        assert surface["reason"] == reason and surface["source"] == source
        if reason == "read_error":
            assert any("RuntimeError" in r.getMessage() for r in caplog.records)
            assert "timed out" not in surface["message"]


def test_route_budget_expiry_keeps_completed_contributors(tmp_path, monkeypatch) -> None:
    """Acceptance 7: the route budget expires after incidents completed."""

    class SlowAfterIncidents(ManagementService):
        def get_human_inbox(self, *args: Any, completed_contributors: Optional[dict] = None, **kwargs: Any) -> Any:
            if completed_contributors is not None:
                completed_contributors["incidents"] = ([_incident(1, "open")], {"status": "ok", "source": "read_store"})
            time.sleep(0.4)
            return super().get_human_inbox(*args, completed_contributors=completed_contributors, **kwargs)

    monkeypatch.setattr(inbox_router, "_get_human_inbox_read_timeout", lambda: 0.1)
    with IncidentOwner([]) as owner, _inbox_app(
        tmp_path, incidents_url=owner.url, service_cls=SlowAfterIncidents,
    ) as (client, _s):
        body = _get(client)
    assert body["meta"]["partial"] is True
    assert body["data"]["summary"]["incident_count"] == 1
    assert body["meta"]["surfaces"]["incidents"]["status"] == "ok"
    assert body["meta"]["surfaces"]["approval_queue"]["reason"] == "read_timeout"


def test_route_budget_outlasts_the_contributor_bound() -> None:
    from services.control_plane.bff.governance.service import human_inbox_surface_timeout_seconds

    assert inbox_router._get_human_inbox_read_timeout() > human_inbox_surface_timeout_seconds()


def test_detail_hiq_and_cockpit_callers_agree_on_open_items(tmp_path) -> None:
    """Acceptance 11."""
    with IncidentOwner(_hosted_incidents()) as owner, _hosted_inbox(tmp_path, owner) as (client, _s):
        listed = _get(client, page_size=200)["data"]["items"]
        open_id = next(i["incident_id"] for i in listed if i["source_type"] == "incident")
        detail = client.get(f"/bff/management/human-inbox/{open_id}", headers=HEADERS)
        resolved = client.get(
            "/bff/management/human-inbox/inc-deployment-comp-deployment-saga-plan-persona-paper-1000",
            headers=HEADERS,
        )
        hiq = client.get("/bff/management/hiq-backlog", headers=HEADERS, params={"page_size": 200})
        cockpit = client.get("/bff/management/cockpit", headers=HEADERS)
    assert detail.status_code == 200
    assert resolved.status_code == 404
    assert hiq.status_code == 200
    assert hiq.json()["data"]["summary"]["total"] == len(listed)
    assert cockpit.status_code == 200
    cockpit_inbox = cockpit.json()["data"]["human_inbox"]
    assert cockpit_inbox["data"]["summary"]["incident_count"] == 7
    assert cockpit_inbox["data"]["summary"]["approval_count"] == 3


def test_inbox_projection_is_shared_by_the_nl_path_and_the_management_route(tmp_path) -> None:
    """Acceptance 4: the NL-ask inbox payload reads and projects through the same
    ``read_open_incidents`` and ``_human_inbox_project_items`` the route uses."""
    from services.control_plane.bff.management_read_models import service as mgmt_service

    snapshot = "2026-11-05T00:00:00Z"
    with IncidentOwner(_hosted_incidents()) as owner, _hosted_inbox(tmp_path, owner) as (client, store):
        route_items = _get(client, page_size=200)["data"]["items"]
        route_paths = list(owner.paths)
        owner.paths.clear()
        incidents = mgmt_service.read_open_incidents(store)
        nl_paths = list(owner.paths)
        approvals = store.list_approval_queue_items()
        reviews = store.list_governance_review_queue_items()
    nl_items = mgmt_service._human_inbox_project_items(
        snapshot_at=snapshot,
        review_records=reviews,
        approval_records=approvals,
        incident_records=incidents,
        persona_rows=[],
    )
    assert route_paths == ["/api/incidents?open_only=true"]
    assert nl_paths == ["/api/incidents?open_only=true"]
    assert sorted(i["id"] for i in nl_items) == sorted(i["id"] for i in route_items)
    by_id = {i["id"]: i for i in mgmt_service._management_prune_camel_aliases(nl_items)}
    for item in route_items:
        assert set(item) == set(by_id[item["id"]]), item["id"]


# ---------------------------------------------------------------------------
# BFF-AI-INBOX-OWNER-20261107: the Management AI reads the inbox through the
# same ManagementService.get_human_inbox builder the route uses.
# ---------------------------------------------------------------------------

_AI_NOW = "2026-11-07T15:15:00Z"


def _ai_cockpit_context(monkeypatch, store: Any, *, identity: Any = None) -> Dict[str, Any]:
    from services.control_plane.bff.assistant import management_service as ms

    service = ManagementService(get_read_store=lambda: store, utc_now=lambda: _AI_NOW)
    monkeypatch.setattr(ms, "_MANAGEMENT_AI_CONTEXT_SERVICE", service)
    monkeypatch.setattr(
        ms, "_build_operator_alerts_payload", lambda _s: {"alerts": [], "meta": {"surfaces": {}}}
    )
    monkeypatch.setattr(
        ms, "_build_management_anomalies_payload", lambda _s: {"items": [], "meta": {"surfaces": {}}}
    )
    return ms._mgmt_nl_collect_context("cockpit", _AI_NOW, identity=identity)


def _ai_inbox_observations(context: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    observation = context["surfaces"]["management_cockpit"]["owner_observation"]
    return {
        o["subject_type"]: o
        for o in observation["contributing_observations"]
        if o.get("owner") == "human_inbox"
    }


@pytest.mark.parametrize("open_incidents", [0, 1])
def test_ai_inbox_matches_the_route_for_an_empty_queue_and_one_open_incident(
    tmp_path, monkeypatch, open_incidents
) -> None:
    """Acceptance 2 and 3: an approval queue read that succeeded with zero
    records is ok for the AI exactly as it is on the route, and the surface
    statuses and item total are the route's."""
    rows = [_incident(i, "open") for i in range(open_incidents)]
    with IncidentOwner(rows) as owner, _inbox_app(tmp_path, incidents_url=owner.url) as (client, store):
        route = _get(client)
        context = _ai_cockpit_context(monkeypatch, store, identity=object())
    route_surfaces = route["meta"]["surfaces"]
    assert route_surfaces["approval_queue"]["status"] == "ok"
    ai = _ai_inbox_observations(context)
    for key in ("approval_queue", "governance_review_queue", "incidents", "persona_readiness", "human_inbox"):
        assert ai[key]["status"] == route_surfaces[key]["status"] == "ok", (key, ai[key])
    assert route["page_info"]["total"] == open_incidents
    assert context["snippets"]["cockpit"]["human_inbox_summary"]["total"] == route["page_info"]["total"]
    assert "Approval queue has no readable source records." not in json.dumps(context["surfaces"])


def test_ai_inbox_total_is_the_page_info_total_not_one_page(tmp_path, monkeypatch) -> None:
    rows = [_incident(i, "open") for i in range(7)]
    with IncidentOwner(rows) as owner, _inbox_app(tmp_path, incidents_url=owner.url) as (client, store):
        route_total = _get(client, page_size=2)["page_info"]["total"]
        context = _ai_cockpit_context(monkeypatch, store)
    assert route_total == 7
    assert context["snippets"]["cockpit"]["human_inbox_summary"]["total"] == 7


def test_ai_inbox_failed_owner_read_is_not_reported_ok(tmp_path, monkeypatch) -> None:
    with _inbox_app(tmp_path, incidents_url=_closed_port_url()) as (_client, store):
        context = _ai_cockpit_context(monkeypatch, store)
    ai = _ai_inbox_observations(context)
    assert ai["incidents"]["status"] != "ok"
    assert ai["human_inbox"]["status"] != "ok"


def test_ai_collect_context_no_longer_uses_the_governance_payload_builder() -> None:
    import inspect

    from services.control_plane.bff.assistant import management_service as ms
    from services.control_plane.bff.governance import human_inbox as governance_inbox

    source = inspect.getsource(ms._mgmt_nl_collect_context)
    assert "get_human_inbox(" in source
    assert "_human_inbox_payload" not in source
    assert not hasattr(governance_inbox, "_human_inbox_payload")
    assert not hasattr(ms, "set_human_inbox_payload")
    # Both transports run the collector off the event loop with the identity.
    for transport in (ms._bff_management_nl_ask_impl, ms._bff_management_nl_ask_stream_impl):
        collected = inspect.getsource(transport)
        assert "asyncio.to_thread(\n            _mgmt_nl_collect_context, focus, now" in collected
        assert "identity=identity" in collected.split("_mgmt_nl_collect_context, focus, now", 1)[1][:120]


def test_context_incidents_successful_empty_open_list_is_ok_through_the_port() -> None:
    with IncidentOwner([_incident(1, "resolved", "high"), _incident(2, "closed", "low")]) as owner:
        store = create_read_surface_ports(
            lifecycle_telemetry_governance=create_lifecycle_telemetry_governance_port(
                incident_port=DomainIncidentPort(incidents_api_url=owner.url),
            ),
        )
        records, observation = ManagementService(read_store=store).get_context_incidents()
        assert owner.paths == ["/api/incidents?open_only=true"]
    assert records == []
    assert observation["status"] == "ok"
    assert not observation.get("degradation_reason")


def test_context_incidents_failed_owner_read_is_unavailable_through_the_port() -> None:
    store = create_read_surface_ports(
        lifecycle_telemetry_governance=create_lifecycle_telemetry_governance_port(
            incident_port=DomainIncidentPort(incidents_api_url=_closed_port_url()),
        ),
    )
    records, observation = ManagementService(read_store=store).get_context_incidents()
    assert records == []
    assert observation["status"] == "unavailable"
    assert "incidents read failed" in observation["degradation_reason"]


def test_context_incidents_open_rows_are_ok_and_returned() -> None:
    with IncidentOwner([_incident(1, "open"), _incident(2, "resolved", "high")]) as owner:
        store = create_read_surface_ports(
            lifecycle_telemetry_governance=create_lifecycle_telemetry_governance_port(
                incident_port=DomainIncidentPort(incidents_api_url=owner.url),
            ),
        )
        records, observation = ManagementService(read_store=store).get_context_incidents()
    assert [r["status"] for r in records] == ["open"]
    assert observation["status"] == "ok"
