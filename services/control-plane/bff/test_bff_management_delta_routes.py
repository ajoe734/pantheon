from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Callable, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.models import utc_now as default_utc_now
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import ReadSurfacePorts


HEADERS = {
    "Authorization": "Bearer op-bff-delta:operator,reviewer",
    "X-Correlation-Id": "corr-bff-management-delta",
}
LOVABLE_ORIGIN = "https://pantheon-dev.lovable.app"
FIXTURE_TENANT_ID = "pantheon-dev"


@pytest.fixture(autouse=True)
def _fixture_tenant(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", FIXTURE_TENANT_ID)


def _load_fallback_data() -> dict[str, Any]:
    fallback_path = os.path.join(os.path.dirname(__file__), "data", "read_surfaces.json")
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class ManagementDeltaTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        if seed_data is not None:
            self._data: dict[str, Any] = seed_data
        elif allow_fallback:
            self._data = _load_fallback_data()
        else:
            self._data = {}
        self.allow_fallback = allow_fallback

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "source": "local_snapshot", "snapshot_at": snapshot_at}

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, [])

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_instances") or self._data.get("runtime_bindings") or self._data.get("runtimes") or []
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtime_instances(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_runtime_bindings(**kwargs)

    def list_runtimes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_runtime_bindings(**kwargs)

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("incidents")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_loop_executions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("loop_executions") or self._data.get("loop_runs") or []
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_loop_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_loop_executions(**kwargs)

    def list_governance_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("governance_audit_events") or self._data.get("audit_log") or []
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_governance_audit_events(**kwargs)

    # Persona reads are tenant-scoped (3f2d13a40, 7959c408d); tag fixture personas with the fixture caller tenant.
    @staticmethod
    def _tenant_tagged(persona: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if persona is None else {"tenant_id": FIXTURE_TENANT_ID, **persona}

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("personas")
        return [self._tenant_tagged(p) for p in (ds.values() if isinstance(ds, dict) else ds)]

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("personas")
        if isinstance(ds, dict):
            return self._tenant_tagged(ds.get(str(persona_id or "")))
        return self._tenant_tagged(
            next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)
        )

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("capability_snapshots")
        if isinstance(ds, dict):
            for cap in ds.values():
                if cap.get("persona_id") == persona_id:
                    return cap
            return ds.get(str(persona_id or ""))
        elif isinstance(ds, list):
            return next((c for c in ds if c.get("persona_id") == persona_id or c.get("id") == persona_id), None)
        return None

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return self.get_capability_snapshot_for_persona(persona_id)

    def get_governance_profile_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("governance_profiles")
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return None

    def get_training_history_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._get_dataset("training_history")
        if isinstance(ds, dict):
            return list(ds.values())
        return list(ds) if isinstance(ds, list) else []

    def get_promotion_record_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("promotion_records")
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return None

    def get_review_history_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._get_dataset("review_history")
        if isinstance(ds, dict):
            return list(ds.values())
        return list(ds) if isinstance(ds, list) else []

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("bindings")
        if isinstance(ds, dict):
            return list(ds.values())
        return list(ds) if isinstance(ds, list) else []

    def get_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("bindings")
        if isinstance(ds, dict):
            return ds.get(str(binding_id or ""))
        return next((b for b in ds if b.get("id") == binding_id or b.get("binding_id") == binding_id), None)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("capital_pools")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_strategy_specs(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("strategy_specs")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_ranking_formulas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("ranking_formulas")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_ranking_formula(self, formula_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("ranking_formulas")
        if isinstance(ds, dict):
            return ds.get(str(formula_id or ""))
        return next((f for f in ds if f.get("id") == formula_id or f.get("formula_id") == formula_id), None)

    def put_ranking_snapshot(self, snapshot: dict[str, Any], **kwargs: Any) -> None:
        ds = self._get_dataset("ranking_snapshots")
        if isinstance(ds, dict):
            ds[snapshot.get("id") or snapshot.get("snapshot_id") or "snap"] = snapshot
        elif isinstance(ds, list):
            ds.append(snapshot)


# ---------------------------------------------------------------------------
# Standalone app harness.
#
# This mirrors the composition root's wiring (see main.py's
# `app.include_router(create_personas_router(...))`,
# `app.include_router(create_capital_router(...))`,
# `app.include_router(create_governance_router(...))`, and
# `app.include_router(create_management_router(...))` calls) but mounts the
# real production routers onto a fresh FastAPI() app built from
# `core.app_factory.build_bff_app` (the same factory main.py uses for CORS,
# security headers, and error-handler wiring) instead of importing main.py
# itself. Every route under test here
# (persona-league/*, incident-timeline, loop-throughput,
# hiq-backlog, quarterly-ranking/*, governance-ledger,
# cost-attribution) is registered by one of these four router factories, not
# by main.py directly.
# ---------------------------------------------------------------------------


def _new_command_store() -> CommandStore:
    tmp_dir = tempfile.mkdtemp(prefix="bff_mgmt_delta_cmd_")
    return CommandStore(os.path.join(tmp_dir, "commands.jsonl"))


def _build_app(
    store: Any,
    *,
    command_store: Optional[CommandStore] = None,
    utc_now_fn: Callable[[], str] = default_utc_now,
) -> FastAPI:
    app = build_bff_app()

    persona_service = PersonaService(
        read_store=store,
        write_owner=store,
        ranking_write_owner=store,
        command_store=command_store or _new_command_store(),
        utc_now_fn=utc_now_fn,
    )
    app.include_router(create_personas_router(service=persona_service))
    app.include_router(
        create_capital_router(
            read_surface=store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now_fn,
        )
    )
    app.include_router(
        create_governance_router(
            read_surface=store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now_fn,
        )
    )
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now_fn,
        )
    )
    return app


def _client_for(store: Any, *, utc_now_fn: Callable[[], str] = default_utc_now) -> TestClient:
    client = TestClient(_build_app(store, utc_now_fn=utc_now_fn), raise_server_exceptions=False)
    client.store = store  # type: ignore[attr-defined]
    return client


def _fresh_client(td: str, *, fallback: bool = True) -> TestClient:
    store = ManagementDeltaTestReadPorts(allow_fallback=fallback)
    command_store = CommandStore(os.path.join(td, "commands.jsonl"))
    client = TestClient(_build_app(store, command_store=command_store), raise_server_exceptions=False)
    client.store = store  # type: ignore[attr-defined]
    return client


def test_persona_league_heatmap() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        response = client.get(
            "/bff/management/persona-league/heatmap",
            headers=HEADERS,
            params={"bucket": "day", "bucket_count": 3, "limit": 5},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]

        assert set(body) == {"data", "page_info", "meta"}
        rows = data["items"]
        buckets = data["buckets"]
        cells = [cell for row in rows for cell in row["cells"]]
        assert len(data["buckets"]) == 3
        assert data["summary"]["bucket"] == "day"
        assert data["summary"]["cell_count"] == len(rows) * len(buckets)
        assert body["meta"]["policy"] == "read_only_governance_advisory"
        assert body["meta"]["surfaces"]["persona_league_heatmap"]["status"] in {"ok", "degraded"}
        assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
        assert len(cells) == data["summary"]["cell_count"]

        alpha = next(row for row in rows if row["persona_id"] == "persona-alpha")
        assert len(alpha["cells"]) == 3
        latest_cell = alpha["cells"][-1]
        assert isinstance(latest_cell["composite_score"], (int, float))
        assert latest_cell["score"] == latest_cell["composite_score"]
        assert latest_cell["overall_score"] == latest_cell["composite_score"]
        assert latest_cell["formula_version"] == "pm12-default-v1"
        assert set(latest_cell["components"]) >= {
            "overall_score",
            "pnl_score",
            "risk_score",
            "execution_score",
            "activity_score",
        }


def test_persona_league_heatmap_requires_auth() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        response = client.get("/bff/management/persona-league/heatmap")

        assert response.status_code == 401, response.text


def test_persona_league_heatmap_cors_preflight() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        response = client.options(
            "/bff/management/persona-league/heatmap",
            headers={
                "Origin": LOVABLE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        assert response.status_code in {200, 204}
        assert response.headers.get("access-control-allow-origin") == LOVABLE_ORIGIN


def _incident_timeline_client() -> TestClient:
    store = ManagementDeltaTestReadPorts(allow_fallback=False)
    incidents = [
        {
            "incident_id": "inc-delta-high",
            "title": "Critical runtime drawdown",
            "severity": "critical",
            "status": "open",
            "created_at": "2026-05-24T09:15:00Z",
            "opened_at": "2026-05-24T09:15:00Z",
            "runtime_id": "runtime-alpha",
            "deployment_plan_id": "plan-alpha",
            "capital_pool_id": "pool-alpha",
            "artifact_id": "artifact-alpha",
            "artifact_version": "v1",
            "telemetry_event_ids": ["tel-high"],
            "evidence_summary": "Drawdown crossed critical threshold.",
        },
        {
            "incident_id": "inc-delta-low",
            "title": "Resolved low-severity audit drift",
            "severity": "low",
            "status": "resolved",
            "created_at": "2026-05-24T07:00:00Z",
            "opened_at": "2026-05-24T07:00:00Z",
            "runtime_id": "runtime-beta",
            "deployment_plan_id": "plan-beta",
            "capital_pool_id": "pool-beta",
        },
        {
            "incident_id": "inc-delta-medium",
            "title": "Medium latency warning",
            "severity": "medium",
            "status": "in_progress",
            "created_at": "2026-05-24T08:30:00Z",
            "opened_at": "2026-05-24T08:30:00Z",
            "runtime_id": "runtime-alpha",
            "deployment_plan_id": "plan-alpha",
            "capital_pool_id": "pool-alpha",
        },
    ]
    store.list_incidents = lambda **_: list(incidents)

    def dataset_source(dataset: str, **_: Any) -> str:
        return "service_store" if dataset == "incidents" else "missing"

    store.dataset_source = dataset_source
    return _client_for(store)


def test_incident_timeline_returns_chronological_bucketed_incidents() -> None:
    client = _incident_timeline_client()

    response = client.get(
        "/bff/management/incident-timeline",
        headers=HEADERS,
        params={"page_size": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]

    assert data["id"] == "management-incident-timeline"
    assert set(body) == {"data", "page_info", "meta"}
    assert set(data) == {"id", "items", "summary", "severity_buckets"}
    assert [item["incident_id"] for item in data["items"]] == [
        "inc-delta-low",
        "inc-delta-medium",
        "inc-delta-high",
    ]
    assert [item["sequence"] for item in data["items"]] == [1, 2, 3]
    assert data["items"][2]["severity_bucket"] == "high"
    assert data["items"][2]["lineage_ref"] == "artifact-alpha@v1"
    assert data["items"][2]["source_refs"]["runtime_ids"] == ["runtime-alpha"]
    assert data["items"][2]["links"]["incident"] == "/bff/incidents/inc-delta-high"
    assert "sourceRefs" not in data["items"][2]
    assert "incidentId" not in data["items"][2]
    assert "severityBucket" not in data["items"][2]
    assert "capitalPool" not in data["items"][2]["links"]

    assert data["severity_buckets"] == {"high": 1, "medium": 1, "low": 1}
    assert data["summary"]["severity_buckets"] == data["severity_buckets"]
    assert data["summary"]["incident_count"] == 3
    assert data["summary"]["active_incident_count"] == 2
    assert data["summary"]["resolved_incident_count"] == 1
    assert data["summary"]["first_incident_at"] == "2026-05-24T07:00:00Z"
    assert data["summary"]["latest_incident_at"] == "2026-05-24T09:15:00Z"
    assert "incidentCount" not in data["summary"]
    assert "severityBuckets" not in data["summary"]
    assert body["page_info"] == {"next_page_token": None, "total": 3, "page_size": 10}
    assert body["meta"]["surfaces"]["incident_timeline"]["source"] == "bff_composed"
    assert body["meta"]["surfaces"]["incidents"]["source"] == "service_store"
    assert body["meta"]["policy"] == "read_only_incident_timeline"
    assert "GET /bff/incidents" in body["meta"]["composition_sources"]


def test_incident_timeline_filters_by_runtime() -> None:
    client = _incident_timeline_client()

    response = client.get(
        "/bff/management/incident-timeline",
        headers=HEADERS,
        params={"runtime_id": "runtime-beta"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["summary"]["incident_count"] == 1
    assert body["data"]["items"][0]["incident_id"] == "inc-delta-low"
    assert body["data"]["severity_buckets"] == {"high": 0, "medium": 0, "low": 1}


def test_incident_timeline_requires_auth() -> None:
    client = _incident_timeline_client()

    response = client.get("/bff/management/incident-timeline")

    assert response.status_code == 401, response.text


def test_incident_timeline_cors_preflight() -> None:
    client = _incident_timeline_client()

    response = client.options(
        "/bff/management/incident-timeline",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"


def _loop_throughput_client() -> TestClient:
    store = ManagementDeltaTestReadPorts(allow_fallback=False)
    loop_runs = [
        {
            "id": "loop-queued",
            "status": "queued",
            "runtime_id": "runtime-alpha",
            "binding_id": "binding-alpha",
            "queued_at": "2026-05-24T10:00:00Z",
        },
        {
            "id": "loop-running",
            "status": "running",
            "runtime_id": "runtime-alpha",
            "binding_id": "binding-alpha",
            "queued_at": "2026-05-24T10:01:00Z",
            "started_at": "2026-05-24T10:03:00Z",
        },
        {
            "id": "loop-completed",
            "status": "completed",
            "runtime_id": "runtime-alpha",
            "binding_id": "binding-alpha",
            "queued_at": "2026-05-24T10:02:00Z",
            "started_at": "2026-05-24T10:04:00Z",
            "completed_at": "2026-05-24T10:08:00Z",
        },
    ]
    store.list_loop_runs = lambda: (True, list(loop_runs))

    def dataset_source(dataset: str, **_: Any) -> str:
        return "service_store" if dataset == "loop_runs" else "missing"

    store.dataset_source = dataset_source
    return _client_for(store)


def test_loop_throughput_reports_queue_depth_lag_and_rate() -> None:
    client = _loop_throughput_client()

    anonymous = client.get("/bff/management/loop-throughput")
    assert anonymous.status_code == 401, anonymous.text

    response = client.get(
        "/bff/management/loop-throughput",
        headers=HEADERS,
        params={"runtime_id": "runtime-alpha", "page_size": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]

    assert data["id"] == "management-loop-throughput"
    assert set(body) == {"data", "page_info", "meta"}
    assert set(data) == {"id", "items", "summary", "metrics"}
    assert body["page_info"] == {"next_page_token": None, "total": 3, "page_size": 10}
    assert data["summary"] == data["metrics"]
    assert data["summary"]["loop_count"] == 3
    assert data["summary"]["queue_depth"] == 1
    assert data["summary"]["active_loop_count"] == 1
    assert data["summary"]["completed_loop_count"] == 1
    assert data["summary"]["runs_per_minute"] == 0.375
    assert data["summary"]["max_queue_lag_seconds"] == 120.0
    assert data["summary"]["average_queue_lag_seconds"] == 120.0
    assert data["summary"]["by_status"] == {"completed": 1, "running": 1, "queued": 1}
    assert data["items"][0]["loop_run_id"] == "loop-completed"
    assert data["items"][0]["queue_lag_seconds"] == 120.0
    assert data["items"][0]["duration_seconds"] == 240.0
    assert data["items"][0]["links"]["loop_run"] == "/bff/v5/loop-runs/loop-completed"
    assert "loopRunId" not in data["items"][0]
    assert "queueLagSeconds" not in data["items"][0]
    assert "sourceRefs" not in data["items"][0]
    assert "loopRun" not in data["items"][0]["links"]
    assert "loopCount" not in data["summary"]
    assert "byStatus" not in data["summary"]
    assert body["meta"]["surfaces"]["loop_throughput"]["source"] == "bff_composed"
    assert body["meta"]["surfaces"]["loop_runs"]["source"] == "service_store"
    assert body["meta"]["policy"] == "read_only_loop_throughput"
    assert "GET /bff/v5/loop-runs" in body["meta"]["composition_sources"]

    queued = client.get(
        "/bff/management/loop-throughput",
        headers=HEADERS,
        params={"status": "queued"},
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["data"]["summary"]["queue_depth"] == 1
    assert queued.json()["data"]["items"][0]["loop_run_id"] == "loop-queued"


def test_loop_throughput_cors_preflight_and_openapi() -> None:
    client = _loop_throughput_client()

    response = client.options(
        "/bff/management/loop-throughput",
        headers={
            "Origin": "https://preview--pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-BFF-Api-Version",
        },
    )

    assert response.status_code == 204, response.text
    assert response.text == ""
    assert response.headers["access-control-allow-origin"] == "https://preview--pantheon-dev.lovable.app"

    schema = client.get("/openapi.json").json()
    assert "/bff/management/loop-throughput" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/loop-throughput"]


def _hiq_backlog_client() -> TestClient:
    store = ManagementDeltaTestReadPorts(allow_fallback=False)
    store.list_approval_queue_items = lambda **_: []
    store._data["incidents"] = [
        {
            "incident_id": "inc-hiq-open-high",
            "kind": "hiq_sentinel",
            "status": "open",
            "severity": "high",
            "title": "HiQ risk incident",
            "created_at": "2026-05-24T10:10:00Z",
        },
        {
            "incident_id": "inc-loop-open",
            "kind": "loop_anomaly",
            "status": "open",
            "severity": "medium",
            "created_at": "2026-05-24T10:00:00Z",
        },
    ]
    return _client_for(store)


def test_hiq_backlog_composes_open_incidents() -> None:
    client = _hiq_backlog_client()

    response = client.get("/bff/management/hiq-backlog", headers=HEADERS, params={"page_size": 10})

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    items = data["items"]

    assert data["id"] == "management-hiq-backlog"
    assert set(body.keys()) == {"data", "page_info", "meta"}
    assert {item["source_id"] for item in items} == {"inc-hiq-open-high", "inc-loop-open"}
    assert all(item["source_type"] == "incident" for item in items)
    assert items[0]["id"].startswith("incident:")


def test_hiq_backlog_filters_and_requires_auth() -> None:
    client = _hiq_backlog_client()

    anonymous = client.get("/bff/management/hiq-backlog")
    assert anonymous.status_code == 401, anonymous.text

    response = client.get(
        "/bff/management/hiq-backlog",
        headers=HEADERS,
        params={"kind": "hiq_sentinel", "source_type": "incident"},
    )

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert [item["source_id"] for item in items] == ["inc-hiq-open-high"]



def test_hiq_backlog_cors_preflight_and_openapi() -> None:
    client = _hiq_backlog_client()
    response = client.options(
        "/bff/management/hiq-backlog",
        headers={
            "Origin": LOVABLE_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-Correlation-Id",
        },
    )

    assert response.status_code in {200, 204}
    assert response.headers["access-control-allow-origin"] == LOVABLE_ORIGIN

    schema = client.get("/openapi.json").json()
    assert "/bff/management/hiq-backlog" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/hiq-backlog"]


def test_quarterly_ranking_drilldown_returns_persona_contribution_breakdown() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)

        anonymous = client.get(
            "/bff/management/quarterly-ranking/drilldown",
            params={"personaId": "persona-alpha", "quarter": "2026-Q1"},
        )
        assert anonymous.status_code == 401, anonymous.text

        response = client.get(
            "/bff/management/quarterly-ranking/drilldown",
            headers=HEADERS,
            params={"personaId": "persona-alpha", "quarter": "2026-Q1"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["X-Correlation-Id"] == "corr-bff-management-delta"
        body = response.json()
        data = body["data"]

        assert data["persona_id"] == "persona-alpha"
        assert data["quarter"] == "2026-Q1"
        assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
        assert data["quarter_window"]["end_exclusive_at"] == "2026-04-01T00:00:00Z"
        assert data["ranking_item"]["persona_id"] == "persona-alpha"
        assert "rankingItem" not in body
        assert "contributionBreakdown" not in body
        assert body["summary"]["persona_id"] == "persona-alpha"
        assert body["summary"]["quarter"] == "2026-Q1"
        assert body["summary"]["component_count"] == 4
        assert body["summary"]["ranked_count"] >= 1
        assert body["summary"]["total_weighted_contribution"] == data["summary"]["total_weighted_contribution"]
        assert "correlationId" not in body["meta"]
        assert body["meta"]["policy"] == "read_only_governance_advisory"
        assert body["meta"]["surfaces"]["quarterly_ranking_drilldown"]["status"] in {"ok", "degraded"}
        assert "GET /bff/management/quarterly-ranking" in body["meta"]["composition_sources"]
        assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]

        contribution_keys = {row["key"] for row in data["contributions"]}
        assert contribution_keys == {"pnl", "risk", "execution", "activity"}
        for row in data["contributions"]:
            assert row["basis"] == "component_score_x_formula_weight"
            assert row["weighted_contribution"] >= 0
            assert 0 <= row["contribution_share"] <= 1


def test_quarterly_ranking_drilldown_accepts_cors_preflight() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        response = client.options(
            "/bff/management/quarterly-ranking/drilldown",
            headers={
                "Origin": LOVABLE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization, X-Correlation-Id",
            },
        )

        assert response.status_code in {200, 204}
        assert response.headers["access-control-allow-origin"] == LOVABLE_ORIGIN
        assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_governance_ledger_unifies_approval_and_override_sources(monkeypatch) -> None:
    # Approvals are owned by the Governance service (503 when unreachable); stub that owner.
    from services.control_plane.bff.governance import approval_owner

    monkeypatch.setattr(
        approval_owner,
        "call_owner",
        lambda method, path, authorization, **kwargs: [
            {"decision_id": "apv-delta-001", "decision_state": "proposed", "version": 1, "evidence_refs": []}
        ],
    )
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        store = client.store  # type: ignore[attr-defined]
        store._data.setdefault("governance_audit_events", []).append(
            {
                "entry_id": "audit-override-001",
                "actor": "operator-jane",
                "action_type": "ManualRiskOverride",
                "target_type": "RebalanceOverride",
                "target_id": "override-001",
                "timestamp": "2026-05-24T14:20:00Z",
                "outcome": "accepted",
                "audit_context": {"reason": "Operator override audit fixture."},
                "evidence_refs": [],
            }
        )

        anonymous = client.get("/bff/management/governance-ledger")
        assert anonymous.status_code == 401, anonymous.text

        response = client.get(
            "/bff/management/governance-ledger",
            headers=HEADERS,
            params={"page_size": 200},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]
        items = data["items"]
        summary = data["summary"]

        assert data["id"] == "management-governance-ledger"
        assert set(body.keys()) == {"data", "page_info", "meta"}
        assert "entries" not in data
        assert "ledger" not in data
        assert body["page_info"]["total"] == summary["ledger_count"]
        assert body["page_info"]["page_size"] == 200
        assert summary["approval_count"] >= 1
        assert summary["override_count"] == 1
        assert summary["by_source_type"]["override"] == 1
        assert summary["policy"] == "read_only_governance_ledger"
        assert body["meta"]["policy"] == "read_only_governance_ledger"
        assert body["meta"]["surfaces"]["governance_ledger"]["source"] == "bff_composed"
        assert "GET /bff/audit" in body["meta"]["composition_sources"]
        assert "GET /bff/approvals" in body["meta"]["composition_sources"]
        assert any(item["source_type"] == "approval" for item in items)
        assert any(item["source_type"] == "override" for item in items)
        assert all("ledgerId" not in item for item in items)
        assert all("sourceType" not in item for item in items)
        assert all("eventType" not in item for item in items)
        assert all("evidenceRefs" not in item for item in items)
        assert all("sourceRecord" not in item for item in items)
        assert all("source_record" not in item for item in items)

        override = client.get(
            "/bff/management/governance-ledger",
            headers=HEADERS,
            params={"source_type": "override"},
        )
        assert override.status_code == 200, override.text
        override_body = override.json()
        assert override_body["data"]["summary"]["ledger_count"] == 1
        assert override_body["data"]["items"][0]["event_type"] == "ManualRiskOverride"


def test_governance_ledger_cors_preflight_and_openapi() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        response = client.options(
            "/bff/management/governance-ledger",
            headers={
                "Origin": LOVABLE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization, X-Correlation-Id",
            },
        )

        assert response.status_code in {200, 204}
        assert response.headers["access-control-allow-origin"] == LOVABLE_ORIGIN

        schema = client.get("/openapi.json").json()
        assert "/bff/management/governance-ledger" in schema["paths"]
        assert "get" in schema["paths"]["/bff/management/governance-ledger"]


def test_cost_attribution_success() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)

        anonymous = client.get("/bff/management/cost-attribution")
        assert anonymous.status_code == 401, anonymous.text

        response = client.get(
            "/bff/management/cost-attribution",
            headers=HEADERS,
            params={"page_size": 20},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]

        # 8226b2774 moved this route to the capital router's owner-backed portfolio
        # projection (tests/test_capital_router.py): list data plus top-level items,
        # no composed summary and no fabricated costs.
        assert set(body) == {"data", "items", "page_info", "meta"}
        assert body["data"] == body["items"]
        assert body["page_info"]["total"] == len(body["items"])
        assert body["meta"]["policy"] == "read_only_cost_attribution"
        assert body["meta"]["total"] == len(body["items"])
        for row in body["items"]:
            assert {"capital_pool_id", "allocation", "cost"} <= set(row)
            assert "costId" not in row
            assert "capitalPoolId" not in row


def test_cost_attribution_filter_by_persona() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)

        response = client.get(
            "/bff/management/cost-attribution",
            headers=HEADERS,
            params={"persona_id": "nonexistent-persona-xyz", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["page_info"]["total"] == 0
        assert body["items"] == []


def test_cost_attribution_cors_preflight_and_openapi() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        response = client.options(
            "/bff/management/cost-attribution",
            headers={
                "Origin": LOVABLE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization, X-Correlation-Id",
            },
        )

        assert response.status_code in {200, 204}
        assert response.headers["access-control-allow-origin"] == LOVABLE_ORIGIN

        schema = client.get("/openapi.json").json()
        assert "/bff/management/cost-attribution" in schema["paths"]
        assert "get" in schema["paths"]["/bff/management/cost-attribution"]


def test_persona_league_and_quarterly_ranking_normalization() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)

        # Test Quarterly Ranking normalization
        response = client.get(
            "/bff/management/quarterly-ranking",
            headers=HEADERS,
            params={"quarter": "2026-Q1"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]
        assert "items" in data
        for item in data["items"]:
            assert "period" in item
            assert item["period"] == "quarter"
            assert "criteria" in item
            assert "governance_state" in item
            assert "eligible" in item
            assert "exclusion_reason" in item or item["exclusion_reason"] is None
            assert "evidence_coverage" in item
            assert "source_confidence" in item

        # Test Persona League Rankings normalization
        response = client.get(
            "/bff/management/persona-league/rankings",
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]
        assert "items" in data
        for block in data["items"]:
            assert "items" in block
            for item in block["items"]:
                assert "period" in item
                assert item["period"] == "short_cycle"
                assert "criteria" in item
                assert "eligible" in item
                assert "exclusion_reason" in item or item["exclusion_reason"] is None
                assert "evidence_coverage" in item
                assert "source_confidence" in item
