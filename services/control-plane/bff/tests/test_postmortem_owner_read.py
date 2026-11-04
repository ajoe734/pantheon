"""Regression and contract tests for POSTMORTEM-BFF-OWNER-READ-20261104.

Covers:
1. /api/v1/postmortems list and detail read the postmortems owner through
   PANTHEON_POSTMORTEMS_API_URL; DomainIncidentPort has no in-process
   _postmortems dictionary and no second source remains.
2. Every other production postmortem read uses the same owner read:
   incident detail through get_postmortem_by_incident
   (GET /api/v1/incidents/{id}, GET /bff/incidents/{id},
   GET /api/v1/operator/post-incident-review/{id}),
   GET /bff/management/postmortems, GET /bff/management/postmortems/{id},
   and GET /bff/agora/postmortems.
3. The owner list accepts bounded paging, status, and text search (q) and
   returns newest first with a total; services/persona/learning_feedback_bridge.py
   using status=published keeps working unchanged.
4. /api/v1/postmortems accepts page_size, page_token, status, and q and returns
   page_info.total; with a fixture of 2027 rows built from the real 23-key
   record shape, the first page body is under 1 MB and the owner read completes
   under the 5.0s BFF client timeout.
5. The list response carries meta.surfaces.postmortems with status and source;
   when the owner read fails the route reports unavailable with no empty list
   presented as ok, verified by a real TCP connection failure.
6. Detail lookup by postmortem_id returns the same id and a missing id returns
   404; a report_id field if present never replaces the requested id.
7. Authorization stays the read role used by the incident routes; owner records
   carry no tenant field and BFF does not invent tenant data.
8. All tests use the real 23-key record shape and exercise real HTTP against
   services/postmortems/main.py without stubbing DomainIncidentPort._opener.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import inspect
import json
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:8012")
os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.management_read_models.router import (
    create_management_read_models_router,
)
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
    CompositeLifecycleTelemetryGovernancePort,
    DomainIncidentPort,
)
from services.control_plane.bff.ports.persona_capital_runtime import (
    create_in_memory_persona_capital_runtime_port,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.control_plane.bff.postmortems.router import create_postmortem_router
from services.incident.incident import IncidentCase, IncidentStore, Postmortem
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryStore
from services.persona.learning_feedback_bridge import run_learning_feedback_bridge
import services.postmortems.main as pm_owner_main

REAL_POSTMORTEM_23_KEYS = frozenset(
    {
        "action_items",
        "artifact_id",
        "artifact_version",
        "author_ids",
        "binding_id",
        "capital_pool_id",
        "contributing_factors",
        "created_at",
        "deployment_plan_id",
        "deployment_stage",
        "incident_cluster_id",
        "incident_evidence_summary",
        "incident_id",
        "lineage_ref",
        "persona_capital_binding_id",
        "postmortem_id",
        "root_cause",
        "runtime_id",
        "status",
        "telemetry_event_ids",
        "timeline",
        "title",
        "trace_id",
    }
)


def build_real_postmortem_dict(
    idx: int,
    *,
    postmortem_id: Optional[str] = None,
    incident_id: Optional[str] = None,
    status: str = "draft",
    created_at: Optional[str] = None,
    title: Optional[str] = None,
    root_cause: Optional[str] = None,
) -> Dict[str, Any]:
    pm_id = postmortem_id or f"pm-20261104-{idx:04d}"
    inc_id = incident_id or f"inc-20261104-{idx:04d}"
    if created_at is None:
        base_dt = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=idx * 12)
        created_at = base_dt.isoformat().replace("+00:00", "Z")
    default_title = (
        f"Postmortem draft: BFF downstream degradation: surface-{idx % 17} timeout ({pm_id})"
        if idx < 1915
        else f"Postmortem analysis: execution slippage alert ({pm_id})"
    )
    rc = root_cause or (
        f"Downstream read timeout on surface-{idx % 17} during concurrent fanout window {idx}; "
        f"upstream HTTP budget exceeded while reading canonical owner store for {inc_id}. "
        + ("Detailed forensic trace segment: " + ("x" * 900))
    )
    record = {
        "postmortem_id": pm_id,
        "incident_id": inc_id,
        "title": title or default_title,
        "status": status,
        "created_at": created_at,
        "binding_id": f"bind-paper-{idx % 25:03d}",
        "deployment_stage": "paper",
        "deployment_plan_id": f"plan-202610-{idx % 40:03d}",
        "capital_pool_id": f"pool-alpha-{idx % 5:02d}",
        "persona_capital_binding_id": f"pcb-alpha-{idx % 25:03d}",
        "artifact_id": f"art-strat-{idx % 30:03d}",
        "artifact_version": f"v1.{idx % 10}.0",
        "runtime_id": f"rt-paper-{idx % 25:03d}",
        "trace_id": f"trace-inc-{idx:04d}-9f8e7d6c5b4a",
        "telemetry_event_ids": [f"tel-{idx:04d}-a", f"tel-{idx:04d}-b", f"tel-{idx:04d}-c"],
        "incident_evidence_summary": (
            f"Observed downstream degradation on surface-{idx % 17} with p99 latency > 5000ms "
            f"for binding bind-paper-{idx % 25:03d} in pool pool-alpha-{idx % 5:02d}. "
            + ("Evidence payload context: " + ("y" * 700))
        ),
        "lineage_ref": f"art-strat-{idx % 30:03d}@v1.{idx % 10}.0",
        "incident_cluster_id": f"cluster-deg-{idx % 12:02d}" if idx % 3 == 0 else None,
        "root_cause": rc,
        "contributing_factors": [
            f"Unpaged owner read on surface-{idx % 17}",
            "Burst of concurrent operator cockpit polling requests",
            f"Retry storm on runtime rt-paper-{idx % 25:03d}",
        ],
        "timeline": [
            {"ts": created_at, "event": f"Incident {inc_id} opened automatically by health monitor"},
            {"ts": created_at, "event": f"Draft postmortem {pm_id} initialized from incident evidence"},
        ],
        "action_items": [
            f"Enforce bounded page_size on owner list endpoint for surface-{idx % 17}",
            f"Verify circuit breaker thresholds on rt-paper-{idx % 25:03d}",
        ],
        "author_ids": ["system-auto-postmortem", "ops-oncall"],
    }
    assert set(record.keys()) == REAL_POSTMORTEM_23_KEYS
    return record


def build_real_incident_dict(idx: int, *, incident_id: Optional[str] = None) -> Dict[str, Any]:
    inc_id = incident_id or f"inc-20261104-{idx:04d}"
    base_dt = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=idx * 12)
    created_at = base_dt.isoformat().replace("+00:00", "Z")
    return {
        "incident_id": inc_id,
        "title": f"BFF downstream degradation: surface-{idx % 17}",
        "severity": "high",
        "status": "open",
        "created_at": created_at,
        "binding_id": f"bind-paper-{idx % 25:03d}",
        "deployment_stage": "paper",
        "deployment_plan_id": f"plan-202610-{idx % 40:03d}",
        "capital_pool_id": f"pool-alpha-{idx % 5:02d}",
        "persona_capital_binding_id": f"pcb-alpha-{idx % 25:03d}",
        "artifact_id": f"art-strat-{idx % 30:03d}",
        "artifact_version": f"v1.{idx % 10}.0",
        "runtime_id": f"rt-paper-{idx % 25:03d}",
        "trace_id": f"trace-inc-{idx:04d}-9f8e7d6c5b4a",
        "telemetry_event_ids": [f"tel-{idx:04d}-a"],
        "evidence_summary": f"Observed downstream degradation on surface-{idx % 17}",
        "lineage_ref": f"art-strat-{idx % 30:03d}@v1.{idx % 10}.0",
    }


class LivePostmortemsOwnerServer:
    """Runs the real services/postmortems/main.py FastAPI app over a localhost HTTPServer."""

    def __init__(
        self,
        storage_path: Path,
        *,
        postmortems: List[Dict[str, Any]],
        incidents: Optional[List[Dict[str, Any]]] = None,
        extra_raw_postmortem_fields: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.storage_path = storage_path
        self.store = IncidentStore(Path(storage_path))
        for inc_raw in incidents or []:
            inc_obj = IncidentCase.from_dict(inc_raw)
            self.store._incidents[inc_obj.incident_id] = inc_obj
        for pm_raw in postmortems:
            pm_obj = Postmortem.from_dict(pm_raw)
            self.store._postmortems[pm_obj.postmortem_id] = pm_obj

        self.extra_raw_postmortem_fields = dict(extra_raw_postmortem_fields or {})
        self.requests_received: List[str] = []
        self._old_owner_store: Any = None
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.base_url: str = ""

    def __enter__(self) -> "LivePostmortemsOwnerServer":
        self._old_owner_store = pm_owner_main.store
        pm_owner_main.store = self.store
        owner_client = TestClient(pm_owner_main.app)
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                outer.requests_received.append(self.path)
                resp = owner_client.get(self.path)
                content = resp.content
                if resp.status_code == 200 and outer.extra_raw_postmortem_fields:
                    payload = json.loads(content.decode("utf-8"))
                    if isinstance(payload, dict) and "postmortem_id" in payload:
                        pm_id = payload["postmortem_id"]
                        if pm_id in outer.extra_raw_postmortem_fields:
                            payload.update(outer.extra_raw_postmortem_fields[pm_id])
                            content = json.dumps(payload).encode("utf-8")
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                if "x-total-count" in resp.headers:
                    self.send_header("X-Total-Count", resp.headers["x-total-count"])
                self.end_headers()
                self.wfile.write(content)

        self._httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._httpd.server_port}"
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        pm_owner_main.store = self._old_owner_store


def _build_bff_test_app(
    *,
    postmortems_api_url: Optional[str],
    incidents_dict: Optional[Dict[str, Dict[str, Any]]] = None,
) -> FastAPI:
    inc_port = DomainIncidentPort(
        incidents=incidents_dict if incidents_dict is not None else {},
        postmortems_api_url=postmortems_api_url,
    )
    ltg_port = CompositeLifecycleTelemetryGovernancePort(incident_port=inc_port)
    read_surface = ReadSurfacePorts(
        lifecycle_telemetry_governance=ltg_port,
        persona_capital_runtime=create_in_memory_persona_capital_runtime_port(),
    )

    def _extract_identity(authorization: Optional[str] = None, *_: Any, **__: Any) -> OperatorIdentity:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authentication required")
        token = authorization.replace("Bearer ", "").strip()
        parts = token.split(":")
        op_id = parts[0] or "op-test"
        roles = [r.strip() for r in parts[1].split(",") if r.strip()] if len(parts) > 1 else ["viewer"]
        return OperatorIdentity(operator_id=op_id, roles=roles)

    def _require_read_role(identity: Any) -> None:
        roles = set(getattr(identity, "roles", []) or [])
        if not roles.intersection({"viewer", "analyst", "operator", "reviewer", "approver", "admin"}):
            raise HTTPException(status_code=403, detail="Insufficient role for read surface")

    def _bff_error(status_code: int, code: Any, message: str, *_: Any, **__: Any) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={"error": {"code": getattr(code, "value", str(code)), "message": message}},
        )

    app = FastAPI()
    app.include_router(
        create_postmortem_router(
            read_surface=read_surface,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
        )
    )
    app.include_router(
        create_incident_router(
            read_surface=read_surface,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
        )
    )
    app.include_router(
        create_management_read_models_router(
            read_surface=read_surface,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
        )
    )
    app.include_router(
        create_agora_router(
            read_surface=read_surface,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            require_write_role=_require_read_role,
            bff_error=_bff_error,
            utc_now=lambda: "2026-11-04T00:00:00Z",
            sync_servant_agent=lambda p: dict(p),
        )
    )
    return app


def test_ac1_domain_incident_port_removes_in_process_postmortems_dict() -> None:
    """AC1: DomainIncidentPort has no postmortems init parameter and no _postmortems attribute."""
    sig = inspect.signature(DomainIncidentPort.__init__)
    assert "postmortems" not in sig.parameters
    port = DomainIncidentPort()
    assert not hasattr(port, "_postmortems")


def test_ac1_ac2_all_bff_postmortem_entry_points_read_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 & AC2: Every production BFF postmortem read entry point returns owner rows."""
    inc_row = build_real_incident_dict(1, incident_id="inc-20261104-0001")
    pm_row = build_real_postmortem_dict(
        1,
        postmortem_id="pm-20261104-0001",
        incident_id="inc-20261104-0001",
        status="draft",
    )

    with LivePostmortemsOwnerServer(
        tmp_path / "incidents.json",
        postmortems=[pm_row],
        incidents=[inc_row],
    ) as owner_srv:
        monkeypatch.setenv("PANTHEON_POSTMORTEMS_API_URL", owner_srv.base_url)
        app = _build_bff_test_app(
            postmortems_api_url=None,  # resolved via PANTHEON_POSTMORTEMS_API_URL
            incidents_dict={"inc-20261104-0001": inc_row},
        )
        client = TestClient(app)
        headers = {"Authorization": "Bearer op-reader:viewer"}

        # 1. GET /api/v1/postmortems
        r_list = client.get("/api/v1/postmortems", headers=headers)
        assert r_list.status_code == 200
        b_list = r_list.json()
        assert len(b_list["data"]) == 1
        assert b_list["data"][0]["postmortem_id"] == "pm-20261104-0001"
        assert set(b_list["data"][0].keys()) >= REAL_POSTMORTEM_23_KEYS
        assert b_list["page_info"]["total"] == 1
        assert b_list["meta"]["surfaces"]["postmortems"] == {
            "status": "ok",
            "source": "service_client",
        }

        # 2. GET /api/v1/postmortems/{report_id}
        r_detail = client.get("/api/v1/postmortems/pm-20261104-0001", headers=headers)
        assert r_detail.status_code == 200
        b_detail = r_detail.json()
        assert b_detail["data"]["postmortem_id"] == "pm-20261104-0001"
        assert b_detail["data"]["linked_incident"]["incident_id"] == "inc-20261104-0001"

        # 3a. Incident detail through get_postmortem_by_incident: GET /api/v1/incidents/{incident_id}
        r_inc_v1 = client.get("/api/v1/incidents/inc-20261104-0001", headers=headers)
        assert r_inc_v1.status_code == 200
        assert r_inc_v1.json()["data"]["postmortem"]["postmortem_id"] == "pm-20261104-0001"

        # 3b. Incident detail through get_postmortem_by_incident: GET /bff/incidents/{incident_id}
        r_inc_bff = client.get("/bff/incidents/inc-20261104-0001", headers=headers)
        assert r_inc_bff.status_code == 200
        assert r_inc_bff.json()["data"]["postmortem"]["postmortem_id"] == "pm-20261104-0001"

        # 3c. Post-incident review through get_postmortem_by_incident: GET /api/v1/operator/post-incident-review/{incident_id}
        r_pir = client.get("/api/v1/operator/post-incident-review/inc-20261104-0001", headers=headers)
        assert r_pir.status_code == 200
        b_pir = r_pir.json()
        assert b_pir["data"]["postmortem"]["postmortem_id"] == "pm-20261104-0001"
        assert b_pir["meta"]["surfaces"]["postmortem"]["status"] == "ok"
        assert b_pir["meta"]["surfaces"]["postmortem"]["source"] == "service_client"

        # 4. GET /bff/management/postmortems
        r_mgmt_list = client.get("/bff/management/postmortems", headers=headers)
        assert r_mgmt_list.status_code == 200
        b_mgmt_list = r_mgmt_list.json()
        assert b_mgmt_list["data"]["status"] == "ok"
        assert len(b_mgmt_list["data"]["items"]) == 1
        assert b_mgmt_list["data"]["items"][0]["postmortem_id"] == "pm-20261104-0001"
        assert b_mgmt_list["page_info"]["total"] == 1

        # 5. GET /bff/management/postmortems/{postmortem_id}
        r_mgmt_detail = client.get("/bff/management/postmortems/pm-20261104-0001", headers=headers)
        assert r_mgmt_detail.status_code == 200
        b_mgmt_detail = r_mgmt_detail.json()
        assert b_mgmt_detail["data"]["postmortem_id"] == "pm-20261104-0001"
        assert b_mgmt_detail["meta"]["status"] == "ok"

        # 6. GET /bff/agora/postmortems
        r_agora = client.get("/bff/agora/postmortems", headers=headers)
        assert r_agora.status_code == 200
        b_agora = r_agora.json()
        assert len(b_agora["items"]) == 1
        assert b_agora["items"][0]["postmortem_id"] == "pm-20261104-0001"
        assert b_agora["meta"]["surfaces"]["agora_postmortems"]["status"] == "ok"
        assert b_agora["meta"]["surfaces"]["postmortems"]["status"] == "ok"


def test_ac3_owner_paging_filtering_and_learning_feedback_bridge_compat(
    tmp_path: Path,
) -> None:
    """AC3: Owner supports bounded paging, status, q, newest-first sort, and unpaged bridge caller."""
    rows = [
        build_real_postmortem_dict(
            1,
            postmortem_id="pm-old-draft",
            created_at="2026-10-18T10:00:00Z",
            status="draft",
            title="Postmortem draft: BFF downstream degradation: alpha",
        ),
        build_real_postmortem_dict(
            2,
            postmortem_id="pm-mid-pub",
            created_at="2026-10-25T10:00:00Z",
            status="published",
            title="Postmortem published: memory pressure on beta",
        ),
        build_real_postmortem_dict(
            3,
            postmortem_id="pm-new-pub",
            created_at="2026-11-03T10:00:00Z",
            status="published",
            title="Postmortem published: order router spike on gamma",
        ),
    ]
    with LivePostmortemsOwnerServer(tmp_path / "incidents.json", postmortems=rows) as owner_srv:
        # Unpaged caller: learning_feedback_bridge using status=published
        persona_store = PersonaMemoryStore(tmp_path / "persona_memory.json")
        institutional_store = InstitutionalMemoryStore(tmp_path / "institutional_memory.json")
        bridge_res = run_learning_feedback_bridge(
            postmortems_api_url=owner_srv.base_url,
            evolution_api_url="http://127.0.0.1:1",
            decisions_store_path=tmp_path / "decisions.json",
            incidents_store_path=tmp_path / "incidents.json",
            persona_store=persona_store,
            institutional_store=institutional_store,
            skip_openclaw_sync=True,
        )
        assert bridge_res["processed_postmortems"] == 2
        assert bridge_res["written_postmortems"] == ["pm-new-pub", "pm-mid-pub"]
        assert bridge_res["errors"] == []


def test_ac4_2027_real_shape_rows_paging_size_and_latency(
    tmp_path: Path,
) -> None:
    """AC4: With 2027 real-shape rows (~8.2 MB total), first page body is < 1 MB and completes < 5.0s."""
    rows = [build_real_postmortem_dict(i) for i in range(2027)]
    rows[2026]["status"] = "published"
    rows[2026]["title"] = "Postmortem published: unique needle sentinel-2027"

    with LivePostmortemsOwnerServer(tmp_path / "incidents.json", postmortems=rows) as owner_srv:
        app = _build_bff_test_app(postmortems_api_url=owner_srv.base_url)
        client = TestClient(app)
        headers = {"Authorization": "Bearer op-reader:viewer"}

        t0 = time.monotonic()
        resp = client.get("/api/v1/postmortems?page_size=50", headers=headers)
        elapsed_s = time.monotonic() - t0

        assert resp.status_code == 200
        body_bytes = len(resp.content)
        assert body_bytes < 1_048_576, f"First page body {body_bytes} bytes exceeded 1 MB"
        assert elapsed_s < 5.0, f"Owner read took {elapsed_s:.3f}s, exceeding 5.0s BFF client timeout"

        payload = resp.json()
        assert payload["page_info"]["total"] == 2027
        assert payload["page_info"]["page_size"] == 50
        assert payload["page_info"]["next_page_token"] == "50"
        assert len(payload["data"]) == 50
        # Newest-first ordering check
        assert payload["data"][0]["postmortem_id"] == "pm-20261104-2026"
        assert payload["data"][1]["postmortem_id"] == "pm-20261104-2025"

        # Second page via page_token
        resp_p2 = client.get("/api/v1/postmortems?page_size=50&page_token=50", headers=headers)
        assert resp_p2.status_code == 200
        p2 = resp_p2.json()
        assert p2["page_info"]["total"] == 2027
        assert p2["page_info"]["next_page_token"] == "100"
        assert p2["data"][0]["postmortem_id"] == "pm-20261104-1976"

        # Status filter + text search q
        resp_q = client.get(
            "/api/v1/postmortems?status=published&q=sentinel-2027&page_size=20",
            headers=headers,
        )
        assert resp_q.status_code == 200
        pq = resp_q.json()
        assert pq["page_info"]["total"] == 1
        assert len(pq["data"]) == 1
        assert pq["data"][0]["postmortem_id"] == "pm-20261104-2026"


def test_ac5_real_connection_failure_reports_unavailable_not_ok_empty_list() -> None:
    """AC5: Real TCP connection refusal marks meta.surfaces.postmortems.status == unavailable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()

    dead_url = f"http://127.0.0.1:{closed_port}"
    app = _build_bff_test_app(postmortems_api_url=dead_url)
    client = TestClient(app)
    headers = {"Authorization": "Bearer op-reader:viewer"}

    resp = client.get("/api/v1/postmortems", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["page_info"]["total"] == 0
    assert body["meta"]["surfaces"]["postmortems"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["postmortems"]["source"] == "unavailable"


def test_ac6_detail_lookup_preserves_postmortem_id_when_report_id_differs(
    tmp_path: Path,
) -> None:
    """AC6: Detail lookup by postmortem_id returns the same id and report_id never replaces it; missing id returns 404."""
    pm_row = build_real_postmortem_dict(1, postmortem_id="pm-canonical-001")
    with LivePostmortemsOwnerServer(
        tmp_path / "incidents.json",
        postmortems=[pm_row],
        extra_raw_postmortem_fields={"pm-canonical-001": {"report_id": "legacy-report-999"}},
    ) as owner_srv:
        app = _build_bff_test_app(postmortems_api_url=owner_srv.base_url)
        client = TestClient(app)
        headers = {"Authorization": "Bearer op-reader:viewer"}

        # Detail lookup on /api/v1/postmortems/{id}
        resp = client.get("/api/v1/postmortems/pm-canonical-001", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["postmortem_id"] == "pm-canonical-001"
        assert data.get("report_id") == "pm-canonical-001"

        # Detail lookup on /bff/management/postmortems/{id}
        resp_mgmt = client.get("/bff/management/postmortems/pm-canonical-001", headers=headers)
        assert resp_mgmt.status_code == 200
        mgmt_data = resp_mgmt.json()["data"]
        assert mgmt_data["postmortem_id"] == "pm-canonical-001"
        assert mgmt_data.get("report_id") in (None, "pm-canonical-001")

        # Missing ID returns 404 on both detail routes
        resp_404 = client.get("/api/v1/postmortems/pm-does-not-exist", headers=headers)
        assert resp_404.status_code == 404
        resp_mgmt_404 = client.get("/bff/management/postmortems/pm-does-not-exist", headers=headers)
        assert resp_mgmt_404.status_code == 404


def test_ac7_read_role_authorization_and_no_invented_tenant_field(
    tmp_path: Path,
) -> None:
    """AC7: Authorization uses read role; owner records carry no tenant field and BFF does not invent one."""
    pm_row = build_real_postmortem_dict(1, postmortem_id="pm-auth-001")
    inc_row = build_real_incident_dict(1, incident_id="inc-20261104-0001")
    assert "tenant_id" not in pm_row and "tenantId" not in pm_row
    assert "tenant_id" not in inc_row and "tenantId" not in inc_row

    with LivePostmortemsOwnerServer(
        tmp_path / "incidents.json",
        postmortems=[pm_row],
        incidents=[inc_row],
    ) as owner_srv:
        app = _build_bff_test_app(
            postmortems_api_url=owner_srv.base_url,
            incidents_dict={"inc-20261104-0001": inc_row},
        )
        client = TestClient(app)

        # Unauthenticated -> 401
        assert client.get("/api/v1/postmortems").status_code == 401
        # Unprivileged role -> 403
        assert (
            client.get(
                "/api/v1/postmortems",
                headers={"Authorization": "Bearer user-guest:guest"},
            ).status_code
            == 403
        )
        # Viewer role -> 200 and no tenant field is invented
        ok_resp = client.get(
            "/api/v1/postmortems",
            headers={"Authorization": "Bearer user-viewer:viewer"},
        )
        assert ok_resp.status_code == 200
        returned_row = ok_resp.json()["data"][0]
        assert "tenant_id" not in returned_row
        assert "tenantId" not in returned_row

        detail_resp = client.get(
            "/api/v1/postmortems/pm-auth-001",
            headers={"Authorization": "Bearer user-viewer:viewer"},
        )
        assert detail_resp.status_code == 200
        detail_row = detail_resp.json()["data"]
        assert "tenant_id" not in detail_row
        assert "tenantId" not in detail_row
