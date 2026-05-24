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
