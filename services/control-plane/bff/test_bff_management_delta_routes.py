from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from ports import ReadSurfacePorts  # noqa: E402


HEADERS = {
    "Authorization": "Bearer op-bff-delta:operator,reviewer",
    "X-Correlation-Id": "corr-bff-management-delta",
}
LOVABLE_ORIGIN = "https://pantheon-dev.lovable.app"


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

    def list_sentinel_findings(self, **kwargs: Any) -> Any:
        ds = self._get_dataset("sentinel_findings")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_findings(self, **kwargs: Any) -> Any:
        return self.list_sentinel_findings(**kwargs)

    def list_v5_interventions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("v5_interventions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

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

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("personas")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("personas")
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

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


def _fresh_client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = ManagementDeltaTestReadPorts(allow_fallback=fallback)
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _sentinel_pulse_client(monkeypatch) -> TestClient:
    store = ManagementDeltaTestReadPorts(allow_fallback=False)
    findings = [
        {
            "id": "finding-critical",
            "finding_id": "finding-critical",
            "kind": "hiq_sentinel",
            "status": "open",
            "severity": "critical",
            "title": "Sentinel capital breach",
            "summary": "Critical sentinel finding for runtime-alpha",
            "runtime_id": "runtime-alpha",
            "incident_id": "incident-alpha",
            "triggered_at": "2026-05-24T08:00:00Z",
        },
        {
            "id": "finding-low",
            "finding_id": "finding-low",
            "kind": "strategy_drift",
            "status": "resolved",
            "severity": "low",
            "title": "Resolved strategy drift",
            "triggered_at": "2026-05-23T08:00:00Z",
        },
    ]
    interventions = [
        {
            "id": "intv-critical",
            "intervention_id": "intv-critical",
            "finding_id": "finding-critical",
            "kind": "hiq_sentinel",
            "status": "pending",
            "severity": "critical",
            "summary": "Review sentinel remediation",
            "triggered_at": "2026-05-24T08:05:00Z",
        }
    ]

    def list_sentinel_findings(
        *,
        kind: str | None = None,
        status: str | None = None,
        severity: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        rows = list(findings)
        if kind:
            rows = [row for row in rows if row["kind"] == kind]
        if status:
            rows = [row for row in rows if row["status"] == status]
        if severity:
            rows = [row for row in rows if row["severity"] == severity]
        return True, rows

    def list_v5_interventions(
        *,
        status: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = list(interventions)
        if status:
            rows = [row for row in rows if row["status"] == status]
        if kind:
            rows = [row for row in rows if row["kind"] == kind]
        return rows

    def dataset_source(dataset: str, **_: Any) -> str:
        if dataset in {"sentinel_findings", "v5_interventions"}:
            return "canonical"
        return "missing"

    store.list_sentinel_findings = list_sentinel_findings
    store.list_v5_interventions = list_v5_interventions
    store.dataset_source = dataset_source
    monkeypatch.setattr(bff_main, "_V5_INTERVENTIONS_STORE", [], raising=False)
    monkeypatch.setattr(bff_main, "read_store", store)
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_sentinel_pulse_composes_findings_and_interventions(monkeypatch) -> None:
    client = _sentinel_pulse_client(monkeypatch)

    response = client.get(
        "/bff/management/sentinel-pulse",
        headers=HEADERS,
        params={"severity": "critical", "q": "runtime-alpha", "page_size": 5},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]

    assert data["id"] == "management-sentinel-pulse"
    assert set(body) == {"data", "page_info", "meta"}
    assert "items" not in body
    assert "findings" not in body
    assert "summary" not in body
    assert body["page_info"] == {"next_page_token": None, "total": 1, "page_size": 5}

    finding = data["items"][0]
    assert finding["finding_id"] == "finding-critical"
    assert finding["severity"] == "critical"
    assert finding["source_refs"]["runtime_id"] == "runtime-alpha"
    assert finding["links"]["finding"] == "/bff/v5/sentinel/findings/finding-critical"

    assert data["related"]["interventions"][0]["intervention_id"] == "intv-critical"
    assert data["summary"]["finding_count"] == 1
    assert data["summary"]["active_finding_count"] == 1
    assert data["summary"]["critical_finding_count"] == 1
    assert data["summary"]["pending_intervention_count"] == 1
    assert data["summary"]["highest_severity"] == "critical"
    assert data["summary"]["policy"] == "read_only_sentinel_pulse"
    assert body["meta"]["surfaces"]["management_sentinel_pulse"]["source"] == "bff_composed"
    assert body["meta"]["surfaces"]["sentinel_findings"]["source"] == "canonical"
    assert body["meta"]["surfaces"]["v5_interventions"]["source"] == "canonical"
    assert "GET /bff/v5/sentinel/findings" in body["meta"]["composition_sources"]


def test_sentinel_pulse_requires_auth(monkeypatch) -> None:
    client = _sentinel_pulse_client(monkeypatch)

    response = client.get("/bff/management/sentinel-pulse")

    assert response.status_code == 401, response.text


def test_sentinel_pulse_cors_preflight() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    response = client.options(
        "/bff/management/sentinel-pulse",
        headers={
            "Origin": LOVABLE_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") == LOVABLE_ORIGIN


def test_persona_league_heatmap() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_persona_league_heatmap_requires_auth() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league/heatmap")

            assert response.status_code == 401, response.text
        finally:
            bff_main.read_store = original


def test_persona_league_heatmap_cors_preflight() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
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


def _incident_timeline_client(monkeypatch) -> TestClient:
    td = tempfile.TemporaryDirectory(prefix="bff_mgmt_incident_timeline_")
    monkeypatch.setattr(bff_main, "_BFF_MGMT_INCIDENT_TIMELINE_TMPDIR", td, raising=False)
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
    monkeypatch.setattr(bff_main, "read_store", store)
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_incident_timeline_returns_chronological_bucketed_incidents(monkeypatch) -> None:
    client = _incident_timeline_client(monkeypatch)

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


def test_incident_timeline_filters_by_runtime(monkeypatch) -> None:
    client = _incident_timeline_client(monkeypatch)

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


def test_incident_timeline_requires_auth(monkeypatch) -> None:
    client = _incident_timeline_client(monkeypatch)

    response = client.get("/bff/management/incident-timeline")

    assert response.status_code == 401, response.text


def test_incident_timeline_cors_preflight(monkeypatch) -> None:
    client = _incident_timeline_client(monkeypatch)

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


def _loop_throughput_client(monkeypatch) -> TestClient:
    td = tempfile.TemporaryDirectory(prefix="bff_mgmt_loop_throughput_")
    monkeypatch.setattr(bff_main, "_BFF_MGMT_LOOP_THROUGHPUT_TMPDIR", td, raising=False)
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
    monkeypatch.setattr(bff_main, "read_store", store)
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_loop_throughput_reports_queue_depth_lag_and_rate(monkeypatch) -> None:
    client = _loop_throughput_client(monkeypatch)

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


def test_loop_throughput_cors_preflight_and_openapi(monkeypatch) -> None:
    client = _loop_throughput_client(monkeypatch)

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


def _hiq_backlog_client(monkeypatch) -> TestClient:
    td = tempfile.TemporaryDirectory(prefix="bff_mgmt_hiq_backlog_")
    monkeypatch.setattr(bff_main, "_BFF_MGMT_HIQ_BACKLOG_TMPDIR", td, raising=False)
    store = ManagementDeltaTestReadPorts(allow_fallback=False)
    sentinel_findings = [
        {
            "finding_id": "sf-hiq-open-high",
            "kind": "hiq_sentinel",
            "status": "open",
            "severity": "high",
            "title": "HiQ sentinel risk alert",
            "description": "Sentinel found a high-risk HIQ condition.",
            "created_at": "2026-05-24T10:10:00Z",
            "runtime_id": "runtime-alpha",
            "incident_id": "inc-hiq-open-high",
        },
        {
            "finding_id": "sf-loop-open",
            "kind": "loop_anomaly",
            "status": "open",
            "severity": "medium",
            "title": "Loop anomaly",
            "created_at": "2026-05-24T10:00:00Z",
        },
    ]
    store.list_approval_queue_items = lambda **_: []
    store.list_sentinel_findings = lambda **_: (True, list(sentinel_findings))

    def dataset_source(dataset: str, **_: Any) -> str:
        if dataset in {"sentinel_findings", "v5_interventions"}:
            return "service_store"
        return "missing"

    store.dataset_source = dataset_source
    monkeypatch.setattr(bff_main, "read_store", store)
    bff_main._V5_INTERVENTIONS_STORE.clear()
    bff_main._V5_INTERVENTIONS_STORE.extend(
        [
            {
                "intervention_id": "intv-hiq-critical",
                "kind": "hiq_sentinel",
                "status": "pending",
                "target_type": "Runtime",
                "target_id": "runtime-alpha",
                "triggered_at": "2026-05-24T11:00:00Z",
                "triggered_by": "sentinel",
                "description": "Critical HIQ sentinel intervention.",
                "correlation_id": "corr-hiq-critical",
            },
            {
                "intervention_id": "intv-risk-high",
                "kind": "risk_breach",
                "status": "escalated",
                "severity": "high",
                "target_type": "CapitalPool",
                "target_id": "pool-alpha",
                "triggered_at": "2026-05-24T10:30:00Z",
                "triggered_by": "risk-radar",
                "description": "Risk breach waiting for operator review.",
            },
            {
                "intervention_id": "intv-loop-anomaly",
                "kind": "loop_anomaly",
                "status": "pending",
                "target_type": "LoopRun",
                "target_id": "loop-alpha",
                "triggered_at": "2026-05-24T10:40:00Z",
            },
            {
                "intervention_id": "intv-hiq-remediated",
                "kind": "hiq_sentinel",
                "status": "remediated",
                "target_type": "Runtime",
                "target_id": "runtime-beta",
                "triggered_at": "2026-05-24T09:30:00Z",
            },
        ]
    )
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_hiq_backlog_composes_open_hiq_interventions_and_findings(monkeypatch) -> None:
    original_store = bff_main.read_store
    original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
    try:
        client = _hiq_backlog_client(monkeypatch)

        response = client.get(
            "/bff/management/hiq-backlog",
            headers=HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]
        items = data["items"]
        summary = data["summary"]
        ids = {item["source_id"] for item in items}

        assert data["id"] == "management-hiq-backlog"
        assert set(body.keys()) == {"data", "page_info", "meta"}
        assert "rows" not in data
        assert "backlog" not in data
        assert ids == {"intv-hiq-critical", "intv-risk-high", "sf-hiq-open-high"}
        assert summary["backlog_count"] == 3
        assert summary["intervention_count"] == 2
        assert summary["sentinel_finding_count"] == 1
        assert summary["by_kind"]["hiq_sentinel"] == 2
        assert summary["by_kind"]["risk_breach"] == 1
        assert body["meta"]["policy"] == "read_only_hiq_backlog"
        assert body["meta"]["surfaces"]["hiq_backlog"]["source"] == "bff_composed"
        assert "GET /bff/v5/interventions" in body["meta"]["composition_sources"]
        assert "GET /bff/v5/sentinel/findings" in body["meta"]["composition_sources"]
        assert "GET /bff/management/human-inbox" in body["meta"]["composition_sources"]

        intervention = next(item for item in items if item["source_id"] == "intv-hiq-critical")
        assert intervention["priority"] == "critical"
        assert intervention["links"]["source"] == "/bff/v5/interventions/intv-hiq-critical"
        assert intervention["links"]["human_inbox"] == (
            "/bff/management/human-inbox/intervention:intv-hiq-critical"
        )
        assert intervention["allowed_actions"]["canRemediate"] is True
        assert "humanInbox" not in intervention["links"]
        assert "allowedActions" not in intervention
        assert "backlogId" not in intervention
        assert "sourceType" not in intervention
        assert "sourceRefs" not in intervention
        assert "sourceRecord" not in intervention
        assert "source_record" not in intervention
    finally:
        bff_main.read_store = original_store
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_hiq_backlog_filters_and_requires_auth(monkeypatch) -> None:
    original_store = bff_main.read_store
    original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
    try:
        client = _hiq_backlog_client(monkeypatch)

        anonymous = client.get("/bff/management/hiq-backlog")
        assert anonymous.status_code == 401, anonymous.text

        response = client.get(
            "/bff/management/hiq-backlog",
            headers=HEADERS,
            params={"kind": "hiq_sentinel", "status": "pending", "source_type": "intervention"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["summary"]["backlog_count"] == 1
        assert body["data"]["items"][0]["source_id"] == "intv-hiq-critical"
    finally:
        bff_main.read_store = original_store
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_hiq_backlog_cors_preflight_and_openapi(monkeypatch) -> None:
    original_store = bff_main.read_store
    original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
    try:
        client = _hiq_backlog_client(monkeypatch)
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
    finally:
        bff_main.read_store = original_store
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def _intervention_stream_client(monkeypatch) -> TestClient:
    td = tempfile.TemporaryDirectory(prefix="bff_mgmt_intervention_stream_")
    monkeypatch.setattr(bff_main, "_BFF_MGMT_INTERVENTION_STREAM_TMPDIR", td, raising=False)
    monkeypatch.setattr(bff_main, "utc_now", lambda: "2026-05-24T12:00:00Z")
    monkeypatch.setattr(bff_main.command_store, "_get_all_commands", lambda: [])
    store = ManagementDeltaTestReadPorts(allow_fallback=False)
    audit_events = [
        {
            "entry_id": "audit-intv-alpha-decision",
            "actor": "operator-jane",
            "action_type": "intervention.decide",
            "target_type": "Intervention",
            "target_id": "intv-alpha",
            "timestamp": "2026-05-24T11:10:00Z",
            "outcome": "accepted",
            "audit_context": {
                "intervention_id": "intv-alpha",
                "persona_id": "persona-alpha",
                "reason": "Operator accepted intervention remediation.",
            },
        }
    ]
    store.list_governance_audit_events = lambda **_: list(audit_events)

    def dataset_source(dataset: str, **_: Any) -> str:
        if dataset in {"v5_interventions", "governance_audit_events"}:
            return "service_store"
        return "missing"

    store.dataset_source = dataset_source
    monkeypatch.setattr(bff_main, "read_store", store)
    bff_main._V5_INTERVENTIONS_STORE.clear()
    bff_main._V5_INTERVENTIONS_STORE.extend(
        [
            {
                "intervention_id": "intv-alpha",
                "kind": "hiq_sentinel",
                "status": "pending",
                "persona_id": "persona-alpha",
                "runtime_id": "runtime-alpha",
                "target_type": "Persona",
                "target_id": "persona-alpha",
                "triggered_at": "2026-05-24T11:00:00Z",
                "triggered_by": "sentinel",
                "description": "Alpha persona needs HIQ review.",
            },
            {
                "intervention_id": "intv-beta",
                "kind": "risk_breach",
                "status": "escalated",
                "persona_id": "persona-beta",
                "runtime_id": "runtime-beta",
                "target_type": "Persona",
                "target_id": "persona-beta",
                "triggered_at": "2026-05-24T10:30:00Z",
                "triggered_by": "risk-radar",
                "description": "Beta persona risk breach escalated.",
            },
            {
                "intervention_id": "intv-old",
                "kind": "hiq_sentinel",
                "status": "pending",
                "persona_id": "persona-old",
                "triggered_at": "2026-05-22T09:00:00Z",
            },
        ]
    )
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_intervention_stream_returns_recent_persona_events(monkeypatch) -> None:
    original_store = bff_main.read_store
    original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
    try:
        client = _intervention_stream_client(monkeypatch)

        response = client.get(
            "/bff/management/intervention-stream",
            headers=HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        data = body["data"]
        items = data["items"]
        summary = data["summary"]

        assert data["id"] == "management-intervention-stream"
        assert set(body.keys()) == {"data", "page_info", "meta"}
        assert "rows" not in data
        assert "events" not in data
        assert "stream" not in data
        assert all("eventId" not in item for item in items)
        assert all("eventSource" not in item for item in items)
        assert all("sourceRefs" not in item for item in items)
        assert all("streamSequence" not in item for item in items)
        assert [item["intervention_id"] for item in items] == [
            "intv-alpha",
            "intv-alpha",
            "intv-beta",
        ]
        assert [item["stream_sequence"] for item in items] == [1, 2, 3]
        assert all("sourceRecord" not in item for item in items)
        assert all("source_record" not in item for item in items)
        assert summary["event_count"] == 3
        assert summary["intervention_count"] == 2
        assert summary["persona_count"] == 2
        assert summary["by_persona"]["persona-alpha"] == 2
        assert summary["by_persona"]["persona-beta"] == 1
        assert summary["window_hours"] == 24
        assert summary["latest_at"] == "2026-05-24T11:10:00Z"
        assert body["meta"]["policy"] == "read_only_intervention_stream"
        assert body["meta"]["surfaces"]["intervention_stream"]["source"] == "bff_composed"
        assert "GET /bff/v5/interventions" in body["meta"]["composition_sources"]
        assert "GET /bff/audit" in body["meta"]["composition_sources"]
    finally:
        bff_main.read_store = original_store
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_intervention_stream_filters_and_requires_auth(monkeypatch) -> None:
    original_store = bff_main.read_store
    original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
    try:
        client = _intervention_stream_client(monkeypatch)

        anonymous = client.get("/bff/management/intervention-stream")
        assert anonymous.status_code == 401, anonymous.text

        response = client.get(
            "/bff/management/intervention-stream",
            headers=HEADERS,
            params={"persona_id": "persona-beta", "status": "escalated"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["summary"]["event_count"] == 1
        assert body["data"]["items"][0]["intervention_id"] == "intv-beta"
        assert body["data"]["items"][0]["persona_id"] == "persona-beta"
    finally:
        bff_main.read_store = original_store
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_intervention_stream_cors_preflight_and_openapi(monkeypatch) -> None:
    original_store = bff_main.read_store
    original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
    try:
        client = _intervention_stream_client(monkeypatch)
        response = client.options(
            "/bff/management/intervention-stream",
            headers={
                "Origin": LOVABLE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization, X-Correlation-Id",
            },
        )

        assert response.status_code in {200, 204}
        assert response.headers["access-control-allow-origin"] == LOVABLE_ORIGIN

        schema = client.get("/openapi.json").json()
        assert "/bff/management/intervention-stream" in schema["paths"]
        assert "get" in schema["paths"]["/bff/management/intervention-stream"]
    finally:
        bff_main.read_store = original_store
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_quarterly_ranking_drilldown_returns_persona_contribution_breakdown() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_quarterly_ranking_drilldown_accepts_cors_preflight() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_governance_ledger_unifies_approval_intervention_and_override_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.append(
                {
                    "intervention_id": "intv-ledger-001",
                    "kind": "hiq_sentinel",
                    "status": "pending",
                    "target_type": "Runtime",
                    "target_id": "runtime-ledger-001",
                    "triggered_at": "2026-05-24T13:30:00Z",
                    "description": "Ledger intervention fixture.",
                }
            )
            bff_main.read_store._data.setdefault("governance_audit_events", []).append(
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
            assert summary["intervention_count"] >= 1
            assert summary["override_count"] == 1
            assert summary["by_source_type"]["override"] == 1
            assert summary["policy"] == "read_only_governance_ledger"
            assert body["meta"]["policy"] == "read_only_governance_ledger"
            assert body["meta"]["surfaces"]["governance_ledger"]["source"] == "bff_composed"
            assert "GET /bff/audit" in body["meta"]["composition_sources"]
            assert "GET /bff/approvals" in body["meta"]["composition_sources"]
            assert "GET /bff/v5/interventions" in body["meta"]["composition_sources"]
            assert any(item["source_type"] == "approval" for item in items)
            assert any(item["source_type"] == "intervention" for item in items)
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
        finally:
            bff_main.read_store = original
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_governance_ledger_cors_preflight_and_openapi() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_cost_attribution_success() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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

            assert set(body) == {"data", "page_info", "meta"}
            assert data["id"] == "management-cost-attribution"
            assert set(data) >= {"items", "summary"}
            assert "rows" not in data
            assert "attributions" not in data
            assert body["page_info"]["page_size"] == 20
            assert data["summary"]["policy"] == "read_only_cost_attribution"
            assert body["meta"]["policy"] == "read_only_cost_attribution"
            assert "cost_attribution" in body["meta"]["surfaces"]
            assert body["meta"]["surfaces"]["cost_attribution"]["source"] == "bff_composed"
            assert "GET /bff/capital-pools" in body["meta"]["composition_sources"]
            assert "row_count" in data["summary"]
            assert "total_cost" in data["summary"]
            assert "rowCount" not in data["summary"]
            assert "totalCost" not in data["summary"]
            if data["items"]:
                row = data["items"][0]
                assert "cost_id" in row
                assert "capital_pool_id" in row
                assert "source_refs" in row
                assert "costId" not in row
                assert "capitalPoolId" not in row
                assert "sourceRefs" not in row
                assert "capitalPool" not in row["links"]
                assert "performanceAttribution" not in row["links"]
        finally:
            bff_main.read_store = original


def test_cost_attribution_pages_groups_before_row_projection(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, fallback=False)
            sources = {
                "runtime_bindings": [{"runtime_id": "runtime-a"}],
                "deployment_plans": [],
                "bindings": [],
                "capital_pools": [
                    {"pool_id": "pool-a", "name": "Pool A", "risk_budget": 100.0},
                    {"pool_id": "pool-b", "name": "Pool B", "risk_budget": 100.0},
                    {"pool_id": "pool-c", "name": "Pool C", "risk_budget": 100.0},
                ],
                "personas": [],
                "strategies": [],
                "plans_by_id": {},
                "bindings_by_id": {},
                "pools_by_id": {
                    "pool-a": {"pool_id": "pool-a", "name": "Pool A", "risk_budget": 100.0},
                    "pool-b": {"pool_id": "pool-b", "name": "Pool B", "risk_budget": 100.0},
                    "pool-c": {"pool_id": "pool-c", "name": "Pool C", "risk_budget": 100.0},
                },
                "personas_by_id": {},
                "strategies_by_id": {},
                "telemetry_by_runtime_id": {"runtime-a": {"runtime_id": "runtime-a"}},
            }
            facts = [
                {
                    "runtime_id": "runtime-a",
                    "capital_pool_id": "pool-a",
                    "persona_id": "persona-a",
                    "strategy_id": "strategy-a",
                    "total_trades": 1,
                    "notional": 1000.0,
                    "avg_slippage_bps": 1.0,
                    "exposure": 100.0,
                    "telemetry_available": True,
                },
                {
                    "runtime_id": "runtime-b",
                    "capital_pool_id": "pool-b",
                    "persona_id": "persona-b",
                    "strategy_id": "strategy-b",
                    "total_trades": 1,
                    "notional": 2000.0,
                    "avg_slippage_bps": 1.0,
                    "exposure": 200.0,
                    "telemetry_available": True,
                },
                {
                    "runtime_id": "runtime-c",
                    "capital_pool_id": "pool-c",
                    "persona_id": "persona-c",
                    "strategy_id": "strategy-c",
                    "total_trades": 1,
                    "notional": 3000.0,
                    "avg_slippage_bps": 1.0,
                    "exposure": 300.0,
                    "telemetry_available": True,
                },
            ]
            projected_fact_counts: list[int] = []
            original_rows = bff_main._management_cost_attribution_rows

            def tracking_rows(projected_facts: list[dict[str, Any]], projected_sources: dict[str, Any]):
                projected_fact_counts.append(len(projected_facts))
                return original_rows(projected_facts, projected_sources)

            monkeypatch.setattr(bff_main, "_pm12_performance_attribution_sources", lambda: sources)
            monkeypatch.setattr(bff_main, "_pm12_performance_attribution_facts", lambda _sources, _period: facts)
            monkeypatch.setattr(bff_main, "_management_cost_attribution_rows", tracking_rows)

            response = client.get(
                "/bff/management/cost-attribution",
                headers=HEADERS,
                params={"page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"]["summary"]["row_count"] == 3
            assert body["data"]["summary"]["returned_row_count"] == 1
            assert body["page_info"] == {"next_page_token": "1", "total": 3, "page_size": 1}
            assert projected_fact_counts == [1]
        finally:
            bff_main.read_store = original


def test_cost_attribution_filter_by_persona() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)

            response = client.get(
                "/bff/management/cost-attribution",
                headers=HEADERS,
                params={"persona_id": "nonexistent-persona-xyz", "page_size": 10},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page_info"]["total"] == 0
            assert body["data"]["items"] == []
        finally:
            bff_main.read_store = original


def test_cost_attribution_cors_preflight_and_openapi() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_persona_league_and_quarterly_ranking_normalization() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original
