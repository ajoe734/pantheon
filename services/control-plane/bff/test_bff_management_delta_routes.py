from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {
    "Authorization": "Bearer op-bff-delta:operator,reviewer",
    "X-Correlation-Id": "corr-bff-management-delta",
}
LOVABLE_ORIGIN = "https://pantheon-dev.lovable.app"


def _fresh_client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=fallback,
    )
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _sentinel_pulse_client(monkeypatch) -> TestClient:
    store = ReadSurfaceStore(
        os.path.join(tempfile.mkdtemp(prefix="bff_sentinel_pulse_"), "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
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
    assert body["items"] == body["findings"] == data["findings"]
    assert data["items"] == body["items"]
    assert data["interventions"] == body["interventions"]
    assert body["page_info"] == {"next_page_token": None, "total": 1, "page_size": 5}

    finding = body["items"][0]
    assert finding["findingId"] == "finding-critical"
    assert finding["severity"] == "critical"
    assert finding["sourceRefs"]["runtimeId"] == "runtime-alpha"
    assert finding["links"]["finding"] == "/bff/v5/sentinel/findings/finding-critical"

    assert body["interventions"][0]["interventionId"] == "intv-critical"
    assert body["summary"]["findingCount"] == 1
    assert body["summary"]["activeFindingCount"] == 1
    assert body["summary"]["criticalFindingCount"] == 1
    assert body["summary"]["pendingInterventionCount"] == 1
    assert body["summary"]["highestSeverity"] == "critical"
    assert body["summary"]["policy"] == "read_only_sentinel_pulse"
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

            assert set(body) >= {"data", "meta"}
            assert body["items"] == data["rows"]
            assert body["rows"] == data["rows"]
            assert body["buckets"] == data["buckets"]
            assert body["cells"] == data["cells"]
            assert len(data["buckets"]) == 3
            assert body["summary"]["bucket"] == "day"
            assert body["summary"]["cellCount"] == len(body["rows"]) * len(body["buckets"])
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_heatmap"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]

            alpha = next(row for row in body["rows"] if row["personaId"] == "persona-alpha")
            assert len(alpha["cells"]) == 3
            latest_cell = alpha["cells"][-1]
            assert isinstance(latest_cell["compositeScore"], (int, float))
            assert latest_cell["score"] == latest_cell["compositeScore"]
            assert latest_cell["overallScore"] == latest_cell["compositeScore"]
            assert latest_cell["formulaVersion"] == "pm12-default-v1"
            assert set(latest_cell["components"]) >= {
                "overallScore",
                "pnlScore",
                "riskScore",
                "executionScore",
                "activityScore",
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
    store = ReadSurfaceStore(
        os.path.join(td.name, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
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
    assert body["items"] == body["rows"] == body["incidents"] == body["events"] == data["items"]
    assert data["rows"] == data["incidents"] == data["events"] == body["items"]
    assert [item["incident_id"] for item in body["items"]] == [
        "inc-delta-low",
        "inc-delta-medium",
        "inc-delta-high",
    ]
    assert [item["sequence"] for item in body["items"]] == [1, 2, 3]
    assert body["items"][2]["severity_bucket"] == "high"
    assert body["items"][2]["lineage_ref"] == "artifact-alpha@v1"
    assert body["items"][2]["sourceRefs"]["runtimeIds"] == ["runtime-alpha"]
    assert body["items"][2]["links"]["incident"] == "/bff/incidents/inc-delta-high"

    assert body["severityBuckets"] == {"high": 1, "medium": 1, "low": 1}
    assert body["summary"]["severityBuckets"] == body["severityBuckets"]
    assert body["summary"]["incident_count"] == 3
    assert body["summary"]["active_incident_count"] == 2
    assert body["summary"]["resolved_incident_count"] == 1
    assert body["summary"]["first_incident_at"] == "2026-05-24T07:00:00Z"
    assert body["summary"]["latest_incident_at"] == "2026-05-24T09:15:00Z"
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
    assert body["summary"]["incident_count"] == 1
    assert body["items"][0]["incident_id"] == "inc-delta-low"
    assert body["severityBuckets"] == {"high": 0, "medium": 0, "low": 1}


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
    store = ReadSurfaceStore(
        os.path.join(td.name, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
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
    assert body["items"] == body["rows"] == body["loops"] == data["items"]
    assert data["rows"] == data["loops"] == body["items"]
    assert body["page_info"] == {"next_page_token": None, "total": 3, "page_size": 10}
    assert body["summary"] == data["summary"] == body["metrics"]
    assert body["summary"]["loop_count"] == 3
    assert body["summary"]["queue_depth"] == 1
    assert body["summary"]["active_loop_count"] == 1
    assert body["summary"]["completed_loop_count"] == 1
    assert body["summary"]["runs_per_minute"] == 0.375
    assert body["summary"]["max_queue_lag_seconds"] == 120.0
    assert body["summary"]["average_queue_lag_seconds"] == 120.0
    assert body["summary"]["by_status"] == {"completed": 1, "running": 1, "queued": 1}
    assert body["items"][0]["loop_run_id"] == "loop-completed"
    assert body["items"][0]["queue_lag_seconds"] == 120.0
    assert body["items"][0]["duration_seconds"] == 240.0
    assert body["items"][0]["links"]["loop_run"] == "/bff/v5/loop-runs/loop-completed"
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
    assert queued.json()["summary"]["queue_depth"] == 1
    assert queued.json()["items"][0]["loop_run_id"] == "loop-queued"


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
    store = ReadSurfaceStore(
        os.path.join(td.name, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
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
        ids = {item["source_id"] for item in body["items"]}

        assert data["id"] == "management-hiq-backlog"
        assert body["items"] == body["rows"] == body["backlog"] == data["items"]
        assert ids == {"intv-hiq-critical", "intv-risk-high", "sf-hiq-open-high"}
        assert body["summary"]["backlog_count"] == 3
        assert body["summary"]["intervention_count"] == 2
        assert body["summary"]["sentinel_finding_count"] == 1
        assert body["summary"]["by_kind"]["hiq_sentinel"] == 2
        assert body["summary"]["by_kind"]["risk_breach"] == 1
        assert body["meta"]["policy"] == "read_only_hiq_backlog"
        assert body["meta"]["surfaces"]["hiq_backlog"]["source"] == "bff_composed"
        assert "GET /bff/v5/interventions" in body["meta"]["composition_sources"]
        assert "GET /bff/v5/sentinel/findings" in body["meta"]["composition_sources"]
        assert "GET /bff/management/human-inbox" in body["meta"]["composition_sources"]

        intervention = next(item for item in body["items"] if item["source_id"] == "intv-hiq-critical")
        assert intervention["priority"] == "critical"
        assert intervention["links"]["source"] == "/bff/v5/interventions/intv-hiq-critical"
        assert intervention["links"]["humanInbox"] == (
            "/bff/management/human-inbox/intervention:intv-hiq-critical"
        )
        assert intervention["allowedActions"]["canRemediate"] is True
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
        assert body["summary"]["backlog_count"] == 1
        assert body["items"][0]["source_id"] == "intv-hiq-critical"
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
    store = ReadSurfaceStore(
        os.path.join(td.name, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
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

        assert data["id"] == "management-intervention-stream"
        assert body["items"] == body["rows"] == body["events"] == body["stream"] == data["items"]
        assert [item["intervention_id"] for item in body["items"]] == [
            "intv-alpha",
            "intv-alpha",
            "intv-beta",
        ]
        assert [item["stream_sequence"] for item in body["items"]] == [1, 2, 3]
        assert body["summary"]["event_count"] == 3
        assert body["summary"]["intervention_count"] == 2
        assert body["summary"]["persona_count"] == 2
        assert body["summary"]["by_persona"]["persona-alpha"] == 2
        assert body["summary"]["by_persona"]["persona-beta"] == 1
        assert body["summary"]["window_hours"] == 24
        assert body["summary"]["latest_at"] == "2026-05-24T11:10:00Z"
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
        assert body["summary"]["event_count"] == 1
        assert body["items"][0]["intervention_id"] == "intv-beta"
        assert body["items"][0]["persona_id"] == "persona-beta"
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

            assert data["personaId"] == "persona-alpha"
            assert data["quarter"] == "2026-Q1"
            assert data["quarterWindow"]["startAt"] == "2026-01-01T00:00:00Z"
            assert data["quarterWindow"]["endExclusiveAt"] == "2026-04-01T00:00:00Z"
            assert data["rankingItem"]["personaId"] == "persona-alpha"
            assert body["rankingItem"] == data["rankingItem"]
            assert body["contributionBreakdown"] == data["contributionBreakdown"]
            assert body["summary"]["personaId"] == "persona-alpha"
            assert body["summary"]["quarter"] == "2026-Q1"
            assert body["summary"]["componentCount"] == 4
            assert body["summary"]["rankedCount"] >= 1
            assert body["summary"]["totalWeightedContribution"] == data["summary"]["totalWeightedContribution"]
            assert body["meta"]["correlationId"] == "corr-bff-management-delta"
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["quarterly_ranking_drilldown"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/quarterly-ranking" in body["meta"]["composition_sources"]
            assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]

            contribution_keys = {row["key"] for row in data["contributions"]}
            assert contribution_keys == {"pnl", "risk", "execution", "activity"}
            for row in data["contributions"]:
                assert row["basis"] == "component_score_x_formula_weight"
                assert row["weightedContribution"] == row["weighted_contribution"]
                assert 0 <= row["contributionShare"] <= 1
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
        try:
            client = _fresh_client(td)
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
                params={"page_size": 20},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            data = body["data"]

            assert data["id"] == "management-governance-ledger"
            assert body["items"] == body["entries"] == data["entries"]
            assert body["ledger"] == body["items"]
            assert body["summary"] == data["summary"]
            assert body["page_info"]["total"] == body["summary"]["ledger_count"]
            assert body["page_info"]["page_size"] == 20
            assert body["summary"]["approval_count"] >= 1
            assert body["summary"]["intervention_count"] >= 1
            assert body["summary"]["override_count"] == 1
            assert body["summary"]["by_source_type"]["override"] == 1
            assert body["summary"]["policy"] == "read_only_governance_ledger"
            assert body["meta"]["policy"] == "read_only_governance_ledger"
            assert body["meta"]["surfaces"]["governance_ledger"]["source"] == "bff_composed"
            assert "GET /bff/audit" in body["meta"]["composition_sources"]
            assert "GET /bff/approvals" in body["meta"]["composition_sources"]
            assert "GET /bff/v5/interventions" in body["meta"]["composition_sources"]
            assert any(item["source_type"] == "approval" for item in body["items"])
            assert any(item["source_type"] == "intervention" for item in body["items"])
            assert any(item["source_type"] == "override" for item in body["items"])

            override = client.get(
                "/bff/management/governance-ledger",
                headers=HEADERS,
                params={"source_type": "override"},
            )
            assert override.status_code == 200, override.text
            override_body = override.json()
            assert override_body["summary"]["ledger_count"] == 1
            assert override_body["items"][0]["event_type"] == "ManualRiskOverride"
        finally:
            bff_main.read_store = original


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

            assert data["id"] == "management-cost-attribution"
            assert body["items"] == body["rows"] == data["rows"]
            assert body["attributions"] == body["items"]
            assert body["summary"] == data["summary"]
            assert body["page_info"]["page_size"] == 20
            assert body["summary"]["policy"] == "read_only_cost_attribution"
            assert body["meta"]["policy"] == "read_only_cost_attribution"
            assert "cost_attribution" in body["meta"]["surfaces"]
            assert body["meta"]["surfaces"]["cost_attribution"]["source"] == "bff_composed"
            assert "GET /bff/capital-pools" in body["meta"]["composition_sources"]
            assert "row_count" in body["summary"]
            assert "total_cost" in body["summary"]
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
            assert body["items"] == []
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
