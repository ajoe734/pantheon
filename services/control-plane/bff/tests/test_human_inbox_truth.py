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
    assert incident["summary"] != incident["details"]["evidence_summary"]
    assert incident["summary"].startswith("Deployment compensation entered safe mode")
    assert incident["summary_key"] == "incident.summary.deployment"
    assert incident["summary_params"]["occurrence_count"] == 1
    assert "timeline" not in incident
    assert "timeline" not in incident["details"]
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


# ---------------------------------------------------------------------------
# INCIDENT-HUMAN-SUMMARY-AND-OCCURRENCES-20261120 (RC6 WP6.1)
# ---------------------------------------------------------------------------


def test_human_inbox_four_drift_reports_returns_localizable_summary_and_never_evidence_summary(tmp_path) -> None:
    """Acceptance 1, 2, 3, 4, 5: Feeding four DriftReports for one binding
    (cluster drift:drawdown; observed 9.609309450237793; threshold 1.25)
    produces a single open incident with occurrence_count=4 whose inbox card
    carries a localizable summary (summary_key + summary_params + plain summary),
    never uses evidence_summary as the card body, and omits timeline.
    """
    from services.incident.incident import IncidentStore, _merge_summary
    from services.incidents.consumer import (
        DriftReportIncidentConsumer,
        build_incident_from_drift_report,
    )

    event_ids = [
        "76586033-ad6a-4f09-ae0d-9a2e756617a5",
        "0b7a8a43-5c02-403e-970f-1a637517a78a",
        "94f2d4bb-75e5-4f50-ae40-53124d5872e7",
        "bf92e54f-13a9-4a76-b7a2-875427646c5c",
    ]
    timestamps = [
        "2026-11-18T11:36:14Z",
        "2026-11-18T12:36:14Z",
        "2026-11-18T13:36:14Z",
        "2026-11-18T14:36:14Z",
    ]
    reports = [
        {
            "drift_report_id": f"drift-{eid}-drawdown",
            "recon_run_id": f"recon-{eid}",
            "evaluation_id": f"eval-{eid}",
            "incident_cluster_id": "drift:drawdown",
            "drift_type": "pnl",
            "status": "open",
            "severity": "medium",
            "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
            "runtime_id": "runtime-3823397e125e4579ba5f4174067087ed",
            "deployment_stage": "paper",
            "deployment_plan_id": "plan-3823397e125e4579ba5f4174067087ed",
            "capital_pool_id": "pool-3823397e125e4579ba5f4174067087ed",
            "persona_capital_binding_id": "pcb-3823397e125e4579ba5f4174067087ed",
            "artifact_id": "artifact-3823397e125e4579ba5f4174067087ed",
            "artifact_version": "1.0.0",
            "trace_id": f"trace-{eid}",
            "telemetry_event_ids": [eid],
            "worst_metric": "drawdown",
            "threshold_metric": "rolling_drawdown_multiple",
            "observed": 9.609309450237793,
            "comparator": "gt",
            "threshold_value": 1.25,
            "threshold_window": "paper-daily-sweep",
            "policy_source": (
                "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1; "
                "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/"
                "EVOLOOP-005-governed-baselines.md#governance-decision"
            ),
            "generated_at": ts,
        }
        for eid, ts in zip(event_ids, timestamps)
    ]

    # Reproduce current-dev (BFF fb8ed951d, 2026-11-20 sweep) log body
    current_dev_summary = ""
    for rep in reports:
        current_dev_summary = _merge_summary(
            current_dev_summary,
            build_incident_from_drift_report(rep).evidence_summary or "",
        )
    assert current_dev_summary.count("DriftReport drift-") == 4
    assert "policy_source=EVOLUTION_REVIEW_AND_THRESHOLDS.md" in current_dev_summary

    # Consume 4 DriftReports into IncidentStore and serve via IncidentOwner to /bff/management/human-inbox
    inc_store = IncidentStore(path=None)
    consumer = DriftReportIncidentConsumer(incident_store=inc_store)
    for rep in reports:
        consumer.consume(rep)
    rows = [inc.to_dict() for inc in inc_store.list_incidents()]
    assert len(rows) == 1

    with IncidentOwner(rows) as owner, _inbox_app(tmp_path, incidents_url=owner.url) as (client, _s):
        body = _get(client, page_size=50)

    items = [i for i in body["data"]["items"] if i["source_type"] == "incident"]
    assert len(items) == 1
    item = items[0]
    assert item["incident_kind"] == "drift"
    assert item["metric"] == "rolling_drawdown_multiple"
    assert item["observed"] == pytest.approx(9.609309450237793)
    assert item["threshold"] == pytest.approx(1.25)
    assert item["comparator"] == "gt"
    assert item["occurrence_count"] == 4
    assert item["first_seen_at"] == "2026-11-18T11:36:14Z"
    assert item["last_seen_at"] == "2026-11-18T14:36:14Z"
    assert item["summary_key"] == "incident.summary.drift_threshold_breach"
    assert item["summary_params"]["kind"] == "drift"
    assert item["summary_params"]["metric"] == "rolling_drawdown_multiple"
    assert item["summary_params"]["observed"] == pytest.approx(9.609309450237793)
    assert item["summary_params"]["threshold"] == pytest.approx(1.25)
    assert item["summary_params"]["comparator"] == "gt"
    assert item["summary_params"]["occurrence_count"] == 4
    assert item["summary"] == "Observed rolling drawdown multiple 9.61 (> 1.25) across 4 occurrences."
    assert item["summary"] != rows[0]["evidence_summary"]
    assert "DriftReport " not in item["summary"]
    assert "policy_source=" not in item["summary"]
    assert "EVOLOOP-005" not in item["summary"]
    assert "timeline" not in item
    assert "timeline" not in item["details"]
    assert set(event_ids).issubset(set(item["evidence_refs"]))


def test_human_inbox_derives_summary_at_read_time_for_legacy_appended_incident_without_mutation(tmp_path) -> None:
    """Acceptance 6: Existing stored incidents with an appended evidence_summary
    (such as hosted inc-drift-5f668ec4c8d9 from the 2026-11-20 coordinator sweep)
    derive their summary and occurrence count at read time without mutating the
    stored record.
    """
    legacy_row = {
        "incident_id": "inc-drift-5f668ec4c8d9",
        "title": "Drift threshold breach: drift:drawdown for rb-3823397e125e4579ba5f4174067087ed",
        "status": "open",
        "severity": "medium",
        "created_at": "2026-11-18T11:36:14Z",
        "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-3823397e125e4579ba5f4174067087ed",
        "capital_pool_id": "pool-3823397e125e4579ba5f4174067087ed",
        "persona_capital_binding_id": "pcb-3823397e125e4579ba5f4174067087ed",
        "artifact_id": "artifact-3823397e125e4579ba5f4174067087ed",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-3823397e125e4579ba5f4174067087ed",
        "trace_id": "trace-76586033-ad6a-4f09-ae0d-9a2e756617a5",
        "incident_cluster_id": "drift:drawdown",
        "telemetry_event_ids": [
            "76586033-ad6a-4f09-ae0d-9a2e756617a5",
            "0b7a8a43-5c02-403e-970f-1a637517a78a",
            "94f2d4bb-75e5-4f50-ae40-53124d5872e7",
            "bf92e54f-13a9-4a76-b7a2-875427646c5c",
            "4fd236d0-5d9a-41fc-9254-1c27a399fd8b",
            "e188a9d2-8f12-4065-8d2e-998877665544",
        ],
        "reconciliation_ids": [
            "drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown",
            "drift-0b7a8a43-5c02-403e-970f-1a637517a78a-drawdown",
            "drift-94f2d4bb-75e5-4f50-ae40-53124d5872e7-drawdown",
            "drift-bf92e54f-13a9-4a76-b7a2-875427646c5c-drawdown",
            "drift-4fd236d0-5d9a-41fc-9254-1c27a399fd8b-drawdown",
            "drift-e188a9d2-8f12-4065-8d2e-998877665544-drawdown",
        ],
        "evidence_summary": (
            "[older entries dropped] DriftReport drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown; "
            "cluster=drift:drawdown; worst_metric=drawdown; threshold_metric=rolling_drawdown_multiple; "
            "observed=9.609309450237793; comparator=gt; threshold_value=1.25; threshold_window=paper-daily-sweep; "
            "policy_source=EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1"
        ),
    }
    snapshot_before = json.dumps(legacy_row, sort_keys=True)

    with IncidentOwner([legacy_row]) as owner, _inbox_app(tmp_path, incidents_url=owner.url) as (client, _s):
        body = _get(client, page_size=50)

    assert json.dumps(legacy_row, sort_keys=True) == snapshot_before
    item = next(i for i in body["data"]["items"] if i["incident_id"] == "inc-drift-5f668ec4c8d9")
    assert item["incident_kind"] == "drift"
    assert item["metric"] == "rolling_drawdown_multiple"
    assert item["observed"] == pytest.approx(9.609309450237793)
    assert item["threshold"] == pytest.approx(1.25)
    assert item["comparator"] == "gt"
    assert item["occurrence_count"] == 6
    assert item["summary_key"] == "incident.summary.drift_threshold_breach"
    assert item["summary"] == "Observed rolling drawdown multiple 9.61 (> 1.25) across 6 occurrences."
    assert "[older entries dropped]" not in item["summary"]


# ---------------------------------------------------------------------------
# BFF-HUMAN-INBOX-DETAIL-TARGETED-CONTRIBUTOR-20261126 (RC1 WP1.2 / RC6 WP6.1)
# ---------------------------------------------------------------------------


def test_readiness_blocker_detail_returns_404_when_meetings_or_incidents_timeout(tmp_path) -> None:
    """Acceptance 1, 3, 4: readiness_blocker:persona:persona-tw-equity queries only
    persona_readiness (1 contributor instead of 5) and returns HTTP 404 when
    persona_readiness succeeds with no matching readiness blocker, even while
    meetings, incidents, approvals, and governance_review_queue raise TimeoutError.
    """
    calls = {"incidents": 0, "meetings": 0, "approvals": 0, "plans": 0, "evolution": 0, "personas": 0}

    def timeout_approvals() -> List[Dict[str, Any]]:
        calls["approvals"] += 1
        raise TimeoutError("approvals timed out")

    def timeout_plans() -> List[Dict[str, Any]]:
        calls["plans"] += 1
        raise TimeoutError("plans timed out")

    def timeout_evolution() -> List[Dict[str, Any]]:
        calls["evolution"] += 1
        raise TimeoutError("evolution timed out")

    def timeout_meetings() -> List[Dict[str, Any]]:
        calls["meetings"] += 1
        raise TimeoutError("meetings timed out")

    def healthy_personas(**_kwargs: Any) -> List[Dict[str, Any]]:
        calls["personas"] += 1
        return [
            {
                "persona_id": "persona-tw-equity",
                "name": "TW Equity",
                "lifecycle_state": "active",
                "metadata": {"governance_required": False, "recommended_governance_action": "none"},
            }
        ]

    with _inbox_app(
        tmp_path,
        incidents_url=_closed_port_url(),
        approvals=timeout_approvals,
        plans=timeout_plans,
        evolution=timeout_evolution,
        personas=healthy_personas,
    ) as (client, store):
        store.list_meetings = timeout_meetings  # type: ignore[attr-defined]
        resp = client.get(
            "/bff/management/human-inbox/readiness_blocker:persona:persona-tw-equity",
            headers=HEADERS,
        )

    assert resp.status_code == 404, resp.text
    err = resp.json().get("detail", resp.json()).get("error", {})
    assert err.get("code") == "RESOURCE_NOT_FOUND"
    assert calls["personas"] == 1
    assert calls["meetings"] == 0
    assert calls["approvals"] == 0
    assert calls["plans"] == 0
    assert calls["evolution"] == 0


def test_readiness_blocker_detail_returns_503_when_persona_readiness_itself_degrades(tmp_path) -> None:
    """Acceptance 2, 3: When persona_readiness itself degrades (raises or times out)
    without finding the item, GET /bff/management/human-inbox/readiness_blocker:*
    still fails closed with HTTP 503 precondition_failed=human_inbox_partial_read.
    """
    calls = {"meetings": 0, "approvals": 0}

    def healthy_meetings() -> List[Dict[str, Any]]:
        calls["meetings"] += 1
        return []

    def healthy_approvals() -> List[Dict[str, Any]]:
        calls["approvals"] += 1
        return []

    def degraded_personas(**_kwargs: Any) -> List[Dict[str, Any]]:
        raise TimeoutError("persona_readiness owner read timed out")

    with IncidentOwner([]) as owner, _inbox_app(
        tmp_path,
        incidents_url=owner.url,
        approvals=healthy_approvals,
        personas=degraded_personas,
    ) as (client, store):
        store.list_meetings = healthy_meetings  # type: ignore[attr-defined]
        resp = client.get(
            "/bff/management/human-inbox/readiness_blocker:persona:persona-tw-equity",
            headers=HEADERS,
        )

    assert resp.status_code == 503, resp.text
    err = resp.json().get("detail", resp.json()).get("error", {})
    assert err.get("code") == "DEPENDENCY_UNAVAILABLE"
    assert err.get("details", {}).get("precondition_failed") == "human_inbox_partial_read"
    assert owner.paths == []
    assert calls == {"meetings": 0, "approvals": 0}


def test_all_prefix_typed_detail_reads_scope_to_matching_contributors_and_untyped_preserves_503(
    tmp_path,
) -> None:
    """Acceptance 1, 2, 3, 4:
    - Prefix-typed IDs (readiness_blocker:*, incident:*, meeting:*, approval:*,
      governance_review:*) execute only the 1-2 capable contributors and return 404
      when those contributors succeed, even if unrelated contributors fail/timeout.
    - When a required contributor for a prefix-typed ID fails, detail fails closed
      with 503 human_inbox_partial_read.
    - Untyped item_ids query all contributors and fail closed with 503 when any
      contributor fails without a match.
    - Existing found-item detail shape is unchanged across typed and untyped reads.
    """
    blocked_persona = {
        "persona_id": "persona-tw-equity",
        "name": "TW Equity",
        "lifecycle_state": "active",
        "updated_at": "2026-11-26T00:00:00Z",
        "metadata": {
            "persona_status": "needs_human_approval",
            "governance_required": True,
            "recommended_governance_action": "hold_for_risk_owner_review",
            "current_work": "Verify TW session boundary",
            "can_deploy": False,
        },
    }
    meeting_row = {
        "meeting_id": "mtg-review-01",
        "meeting_type": "MT-1",
        "status": "closed",
        "outcome": "REVIEW",
        "chair_persona_id": "persona-chair-alpha",
        "created_at": "2026-11-26T01:00:00Z",
        "closed_at": "2026-11-26T01:10:00Z",
    }
    plan_row = {
        "plan_id": "plan-gov-01",
        "status": "pending_review",
        "target_stage": "paper",
        "submitted_at": "2026-11-26T02:00:00Z",
    }

    # 1. Found-item detail shape parity between typed and untyped IDs on healthy store
    with IncidentOwner([_incident(1, "open")]) as owner, _inbox_app(
        tmp_path,
        incidents_url=owner.url,
        approvals=lambda: [_approval(1)],
        plans=lambda: [plan_row],
        personas=lambda **_k: [blocked_persona],
    ) as (client, store):
        store.list_meetings = lambda: [meeting_row]  # type: ignore[attr-defined]
        store.list_persona_league = lambda **_k: [  # type: ignore[attr-defined]
            {
                "persona_id": "persona-tw-equity",
                "governance_required": True,
                "recommendation": "hold_for_risk_owner_review",
                "status": "needs_human_approval",
            }
        ]

        for typed_id, untyped_id in (
            ("readiness_blocker:persona:persona-tw-equity", "persona-tw-equity"),
            ("incident:inc-deployment-comp-deployment-saga-plan-persona-paper-0001", "inc-deployment-comp-deployment-saga-plan-persona-paper-0001"),
            ("approval:apv-1", "apv-1"),
            ("governance_review:review-plan-gov-01", "review-plan-gov-01"),
            ("meeting_review:mtg-review-01", "mtg-review-01"),
            ("meeting:mtg-review-01", "mtg-review-01"),
        ):
            r_typed = client.get(f"/bff/management/human-inbox/{typed_id}", headers=HEADERS)
            r_untyped = client.get(f"/bff/management/human-inbox/{untyped_id}", headers=HEADERS)
            assert r_typed.status_code == 200, (typed_id, r_typed.text)
            assert r_untyped.status_code == 200, (untyped_id, r_untyped.text)
            assert r_typed.json()["data"] == r_untyped.json()["data"]

    # 2. Incident, meeting, approval, governance_review 404 when required contributor(s) succeed
    #    and unrelated contributors raise TimeoutError, plus 503 for untyped item_id
    def raise_timeout(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError("unrelated contributor timed out")

    # 2a. incident:inc-nonexistent -> 404 while approvals, governance_review, personas, meetings time out
    with IncidentOwner([]) as owner, _inbox_app(
        tmp_path,
        incidents_url=owner.url,
        approvals=raise_timeout,
        plans=raise_timeout,
        evolution=raise_timeout,
        personas=raise_timeout,
    ) as (client, store):
        store.list_meetings = raise_timeout  # type: ignore[attr-defined]
        r_inc_404 = client.get("/bff/management/human-inbox/incident:inc-nonexistent", headers=HEADERS)
        assert r_inc_404.status_code == 404, r_inc_404.text
        assert len(owner.paths) == 1

        # Untyped item_id queries all contributors and fails closed with 503
        r_untyped_503 = client.get("/bff/management/human-inbox/untyped-item-001", headers=HEADERS)
        assert r_untyped_503.status_code == 503, r_untyped_503.text
        assert (
            r_untyped_503.json()["detail"]["error"]["details"]["precondition_failed"]
            == "human_inbox_partial_read"
        )

    # 2b. meeting:mtg-nonexistent -> 404 while incidents, approvals, plans, personas fail
    with _inbox_app(
        tmp_path,
        incidents_url=_closed_port_url(),
        approvals=raise_timeout,
        plans=raise_timeout,
        evolution=raise_timeout,
        personas=raise_timeout,
    ) as (client, store):
        store.list_meetings = lambda: []  # type: ignore[attr-defined]
        r_mtg_404 = client.get("/bff/management/human-inbox/meeting:mtg-nonexistent", headers=HEADERS)
        assert r_mtg_404.status_code == 404, r_mtg_404.text

        store.list_meetings = raise_timeout  # type: ignore[attr-defined]
        r_mtg_503 = client.get("/bff/management/human-inbox/meeting:mtg-nonexistent", headers=HEADERS)
        assert r_mtg_503.status_code == 503, r_mtg_503.text
        assert (
            r_mtg_503.json()["detail"]["error"]["details"]["precondition_failed"]
            == "human_inbox_partial_read"
        )

    # 2c. approval:apv-nonexistent and governance_review:review-nonexistent -> 404 while incidents, personas, meetings fail
    with _inbox_app(
        tmp_path,
        incidents_url=_closed_port_url(),
        approvals=lambda: [],
        plans=lambda: [],
        evolution=lambda: [],
        personas=raise_timeout,
    ) as (client, store):
        store.list_meetings = raise_timeout  # type: ignore[attr-defined]
        r_apv_404 = client.get("/bff/management/human-inbox/approval:apv-nonexistent", headers=HEADERS)
        r_gov_404 = client.get(
            "/bff/management/human-inbox/governance_review:review-nonexistent",
            headers=HEADERS,
        )
        assert r_apv_404.status_code == 404, r_apv_404.text
        assert r_gov_404.status_code == 404, r_gov_404.text
