"""SENT-001: Focused contract tests for /bff/v5/sentinel/findings query filters.

Covers kind, status, and severity query parameter filtering, invalid filter
validation (400), and the kind field in the SentinelFinding derived model.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402

HEADERS = {"Authorization": "Bearer op-execute-plans:operator,reviewer,admin:mfa"}

# Incidents covering all valid kind/status/severity values.
# Titles deliberately avoid "loop" keyword so all four records appear as sentinel findings.
_INCIDENT_SEED = {
    "sf-hiq-open-critical": {
        "incident_id": "sf-hiq-open-critical",
        "title": "HiQ Sentinel Alert",
        "kind": "hiq_sentinel",
        "status": "open",
        "severity": "critical",
        "runtime_id": "rt-1",
        "binding_id": "binding-1",
    },
    "sf-risk-resolved-high": {
        "incident_id": "sf-risk-resolved-high",
        "title": "Risk Breach Detected",
        "kind": "risk_breach",
        "status": "resolved",
        "severity": "high",
        "runtime_id": "rt-2",
        "binding_id": "binding-2",
    },
    "sf-drift-dismissed-medium": {
        "incident_id": "sf-drift-dismissed-medium",
        "title": "Strategy Drift Observation",
        "kind": "strategy_drift",
        "status": "dismissed",
        "severity": "medium",
        "runtime_id": "rt-3",
        "binding_id": "binding-3",
    },
    "sf-anomaly-escalated-low": {
        "incident_id": "sf-anomaly-escalated-low",
        # Title avoids "loop" so it is classified as a sentinel finding, not a loop run.
        "title": "Sentinel Anomaly Escalated",
        "kind": "loop_anomaly",
        "status": "escalated",
        "severity": "low",
        "runtime_id": "rt-4",
        "binding_id": "binding-4",
    },
}


@contextmanager
def _store(*, seed: dict = _INCIDENT_SEED) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        snap = os.path.join(td, "read_surfaces.json")
        with open(snap, "w") as f:
            json.dump({"incidents": seed}, f)
        original = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(snap, allow_local_snapshot_fallback=True)
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# kind field in derived model
# ---------------------------------------------------------------------------

def test_sentinel_finding_derived_model_has_kind_field(monkeypatch):
    """SentinelFinding derived from an incident must expose the kind field."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings/sf-hiq-open-critical", headers=HEADERS)
    assert r.status_code == 200, r.text
    data = r.json().get("data", {})
    assert "kind" in data, f"kind field missing in SentinelFinding: {data}"
    assert data["kind"] == "hiq_sentinel"


def test_sentinel_finding_kind_inferred_from_title_when_field_absent(monkeypatch):
    """When the incident has no kind field, _infer_sentinel_kind derives it from title keywords."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    seed = {
        "sf-inferred": {
            "incident_id": "sf-inferred",
            "title": "Capital Risk Breach Alert",  # matches risk_breach keywords
            "status": "open",
            "severity": "high",
        }
    }
    with _store(seed=seed) as client:
        r = client.get("/bff/v5/sentinel/findings/sf-inferred", headers=HEADERS)
    assert r.status_code == 200, r.text
    data = r.json().get("data", {})
    assert data.get("kind") == "risk_breach", f"expected risk_breach inferred kind, got: {data}"


# ---------------------------------------------------------------------------
# kind filter
# ---------------------------------------------------------------------------

def test_sentinel_findings_kind_filter_returns_matching_records(monkeypatch):
    """`?kind=hiq_sentinel` must return only hiq_sentinel records."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?kind=hiq_sentinel", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1, f"expected 1 item, got {len(items)}: {items}"
    assert items[0]["id"] == "sf-hiq-open-critical"
    assert items[0]["kind"] == "hiq_sentinel"


def test_sentinel_findings_kind_filter_excludes_other_kinds(monkeypatch):
    """`?kind=risk_breach` must exclude records with other kinds."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?kind=risk_breach", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [item["id"] for item in items]
    assert "sf-risk-resolved-high" in ids
    assert "sf-hiq-open-critical" not in ids
    assert "sf-drift-dismissed-medium" not in ids


def test_sentinel_findings_kind_filter_case_insensitive(monkeypatch):
    """`?kind=HiQ_Sentinel` must be treated the same as `?kind=hiq_sentinel`."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?kind=HiQ_Sentinel", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "sf-hiq-open-critical"


def test_sentinel_findings_invalid_kind_returns_400(monkeypatch):
    """`?kind=unknown_kind` must return 400 with an informative error."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?kind=unknown_kind", headers=HEADERS)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# status filter
# ---------------------------------------------------------------------------

def test_sentinel_findings_status_filter_returns_matching_records(monkeypatch):
    """`?status=resolved` must return only resolved records."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?status=resolved", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "sf-risk-resolved-high"
    assert items[0]["status"] == "resolved"


def test_sentinel_findings_status_filter_open(monkeypatch):
    """`?status=open` must return only open records."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?status=open", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [item["id"] for item in items]
    assert "sf-hiq-open-critical" in ids
    assert "sf-risk-resolved-high" not in ids


def test_sentinel_findings_invalid_status_returns_400(monkeypatch):
    """`?status=pending` must return 400."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?status=pending", headers=HEADERS)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# severity filter
# ---------------------------------------------------------------------------

def test_sentinel_findings_severity_filter_returns_matching_records(monkeypatch):
    """`?severity=critical` must return only critical records."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?severity=critical", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "sf-hiq-open-critical"
    assert items[0]["severity"] == "critical"


def test_sentinel_findings_severity_filter_excludes_others(monkeypatch):
    """`?severity=low` must exclude critical/high/medium records."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?severity=low", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "sf-anomaly-escalated-low"


def test_sentinel_findings_invalid_severity_returns_400(monkeypatch):
    """`?severity=extreme` must return 400."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings?severity=extreme", headers=HEADERS)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# combined filters
# ---------------------------------------------------------------------------

def test_sentinel_findings_combined_kind_and_severity_filter(monkeypatch):
    """`?kind=hiq_sentinel&severity=critical` must return only matching record."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get(
            "/bff/v5/sentinel/findings?kind=hiq_sentinel&severity=critical",
            headers=HEADERS,
        )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "sf-hiq-open-critical"


def test_sentinel_findings_combined_filters_no_match_returns_empty(monkeypatch):
    """Filters that match no record must return an empty items list, not an error."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get(
            "/bff/v5/sentinel/findings?kind=hiq_sentinel&severity=low",
            headers=HEADERS,
        )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items == [], f"expected empty list, got: {items}"


def test_sentinel_findings_no_filter_returns_all_records(monkeypatch):
    """Without filters all non-loop incidents are returned as sentinel findings.

    The list may also include additive ``persona_health`` findings emitted by
    the HealthReasonCode rule engine (Card P2-16) for any degraded persona in
    the store, so the incident-derived ids are asserted as a subset and any
    extra ids are required to be persona_health rule-engine findings.
    """
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _store() as client:
        r = client.get("/bff/v5/sentinel/findings", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = {item["id"] for item in items}
    incident_ids = {
        "sf-hiq-open-critical",
        "sf-risk-resolved-high",
        "sf-drift-dismissed-medium",
        "sf-anomaly-escalated-low",
    }
    assert incident_ids <= ids, f"missing incident-derived ids: {incident_ids - ids}"
    extra = [item for item in items if item["id"] not in incident_ids]
    assert all(item.get("kind") == "persona_health" for item in extra), (
        f"unexpected non-health extra findings: {extra}"
    )


# ---------------------------------------------------------------------------
# OpenAPI regression: kind/status/severity query params must be in /openapi.json
# ---------------------------------------------------------------------------

def test_openapi_sentinel_findings_list_has_filter_query_params():
    """/openapi.json must document kind, status, and severity as query params for GET /bff/v5/sentinel/findings.

    Regression guard against the generic alias re-registering the path and
    overwriting the dedicated filtered route in the OpenAPI schema.
    """
    spec = bff_main.app.openapi()
    get_op = spec["paths"]["/bff/v5/sentinel/findings"]["get"]
    operation_id = get_op.get("operationId", "")
    params = {p["name"] for p in get_op.get("parameters", [])}
    assert "kind" in params, (
        f"OpenAPI GET /bff/v5/sentinel/findings missing 'kind' query param. "
        f"operationId={operation_id!r}, params={sorted(params)}"
    )
    assert "status" in params, (
        f"OpenAPI GET /bff/v5/sentinel/findings missing 'status' query param. "
        f"operationId={operation_id!r}, params={sorted(params)}"
    )
    assert "severity" in params, (
        f"OpenAPI GET /bff/v5/sentinel/findings missing 'severity' query param. "
        f"operationId={operation_id!r}, params={sorted(params)}"
    )
    assert "sem_final_generic_read_alias" not in operation_id, (
        f"OpenAPI GET /bff/v5/sentinel/findings is owned by the generic alias "
        f"(operationId={operation_id!r}); duplicate route registration detected."
    )
