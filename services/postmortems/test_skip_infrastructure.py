"""POSTMORTEM-SKIP-INFRA-DRAFTS-20261125: infra incidents produce no draft; old drafts hidden with a count.

Rows use the real hosted shapes (infra-bff, monitor-agent, drift), not toy rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from services.incident.incident import IncidentCase, IncidentStatus, Postmortem
from services.postmortems.consumer import build_postmortem_draft_from_incident
from services.postmortems.main import app, store

client = TestClient(app)
CONSUME = "/api/postmortems/consume-resolved-incident"


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    monkeypatch.setattr("services.postmortems.main._publish_postmortem_to_evolution_if_needed", lambda i: None)
    for _ in (0,):
        store._incidents.clear(); store._postmortems.clear()
        path = getattr(store, "_path", None)
        if path is not None and path.exists():
            path.unlink()
        store._loaded_mtime_ns = None
    yield
    store._incidents.clear(); store._postmortems.clear()
    path = getattr(store, "_path", None)
    if path is not None and path.exists():
        path.unlink()
    store._loaded_mtime_ns = None


def _inc(incident_id, **kw) -> IncidentCase:
    base = dict(
        incident_id=incident_id, title="t", status=IncidentStatus.RESOLVED.value,
        severity="high", created_at="2026-11-25T06:00:00Z", resolved_at="2026-11-25T06:30:00Z",
        deployment_stage="paper", capital_pool_id="infra-tenant-tenant-dev",
        artifact_id="a", artifact_version="1", runtime_id="r", trace_id="tr",
    )
    base.update(kw)
    return IncidentCase(**base)


def infra_bff(incident_id="inc-infra-bff"):
    return _inc(
        incident_id,
        binding_id="infra-subject-tenant-dev-control-plane-bff-reconciliation-drift",
        deployment_plan_id="infra-plan-control-plane-bff",
        persona_capital_binding_id="infra-authority-control-plane-bff",
        incident_cluster_id="infrastructure:tenant-dev:control-plane-bff:downstream_degradation",
        evidence_summary="non_trading_infrastructure_incident=true downstream degradation",
        title="downstream degradation",
    )


def infra_summary_only(incident_id="inc-infra-sum"):
    # classified only through evidence_summary: proves the stored
    # incident_evidence_summary field name is mapped onto the predicate.
    return _inc(
        incident_id,
        binding_id="infra-subject-tenant-dev-x",
        deployment_plan_id="infra-plan-x",
        persona_capital_binding_id="infra-authority-x",
        incident_cluster_id="other",
        evidence_summary="non_trading_infrastructure_incident=true",
    )


def monitor_agent(incident_id="agent-finding-1"):
    return _inc(
        incident_id,
        binding_id="infra-binding-monitor-agent",
        deployment_plan_id="infra-plan-monitor-agent",
        persona_capital_binding_id="infra-authority-monitor-agent",
        incident_cluster_id="infra-cluster:monitor-agent:agent-finding-1",
        artifact_id="infra-component-agent-finding-1",
        evidence_summary="component_kind=monitor-agent-finding",
    )


def drift(incident_id="inc-drift-1"):
    return _inc(
        incident_id, binding_id="binding-1", deployment_plan_id="plan-1",
        persona_capital_binding_id="pcb-1", capital_pool_id="pool-1",
        incident_cluster_id="drift:rolling_drawdown_multiple",
        evidence_summary="Drift threshold breached",
    )


def _seed_pm(inc: IncidentCase, status="draft"):
    store._incidents[inc.incident_id] = inc
    pm = build_postmortem_draft_from_incident(inc)
    d = pm.to_dict(); d["status"] = status
    pm = Postmortem.from_dict(d)
    store._postmortems[pm.postmortem_id] = pm
    return pm


@pytest.mark.parametrize("maker", [infra_bff, infra_summary_only, monitor_agent])
def test_infra_incident_skipped_no_draft_and_redelivery_same(maker):
    inc = maker()
    store._incidents[inc.incident_id] = inc
    for _ in range(2):
        r = client.post(CONSUME, json={"incident_id": inc.incident_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skipped"] is True and body["reason"] == "infrastructure_incident"
    assert store.list_postmortems() == []


def _event(incident_id):
    from services.postmortems.test_evochain_003_delivery import _resolved_incident_event
    return _resolved_incident_event(incident_id)


def test_infra_event_delivery_reserves_no_inbox_row_and_redelivery_same(monkeypatch):
    from services.postmortems import main as m
    inc = infra_bff(); store._incidents[inc.incident_id] = inc
    reserved = []
    real = m.inbox_store.reserve
    monkeypatch.setattr(m.inbox_store, "reserve", lambda *a, **k: reserved.append(1) or real(*a, **k))
    ev = _event(inc.incident_id)
    for _ in range(2):
        r = client.post(CONSUME, json={"incident_id": inc.incident_id, "event": ev.to_dict()})
        assert r.status_code == 200, r.text
        assert r.json()["skipped"] is True
    assert reserved == []
    assert store.list_postmortems() == []


def test_existing_postmortem_of_infra_incident_left_unchanged():
    inc = infra_bff(); pm = _seed_pm(inc, status="draft")
    before = store.get_postmortem(pm.postmortem_id).to_dict()
    r = client.post(CONSUME, json={"incident_id": inc.incident_id})
    assert r.status_code == 200 and r.json()["skipped"] is True
    assert store.get_postmortem(pm.postmortem_id).to_dict() == before


def test_drift_incident_still_creates_draft():
    inc = drift(); store._incidents[inc.incident_id] = inc
    r = client.post(CONSUME, json={"incident_id": inc.incident_id})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"


def _seed_mixed():
    _seed_pm(infra_bff("i1")); _seed_pm(infra_summary_only("i2")); _seed_pm(monitor_agent("agent-2"))
    _seed_pm(infra_bff("i-rev"), status="review")
    _seed_pm(infra_bff("i-pub"), status="published")
    _seed_pm(drift("d1")); _seed_pm(drift("d2"))


def test_list_hides_infra_drafts_with_count_and_paging():
    _seed_mixed()
    r = client.get("/api/postmortems", params={"page_size": 2})
    b = r.json()
    assert b["total"] == 4 and b["hidden_infrastructure_count"] == 3
    assert len(b["items"]) == 2 and b["next_page_token"] == "2"
    ids = {p["incident_id"] for p in client.get("/api/postmortems", params={"page_size": 50}).json()["items"]}
    assert ids == {"i-rev", "i-pub", "d1", "d2"}  # reviewed/published infra never hidden


def test_list_include_infrastructure_and_incident_filter_show_all():
    _seed_mixed()
    b = client.get("/api/postmortems", params={"page_size": 50, "include_infrastructure": "true"}).json()
    assert b["total"] == 7 and b["hidden_infrastructure_count"] == 0
    b = client.get("/api/postmortems", params={"page_size": 50, "incident_id": "i1"}).json()
    assert b["total"] == 1 and b["hidden_infrastructure_count"] == 0


def test_unpaged_list_hides_and_reports_header():
    _seed_mixed()
    r = client.get("/api/postmortems")
    assert len(r.json()) == 4 and r.headers["X-Hidden-Infrastructure-Count"] == "3"


def test_total_exact_at_6000_rows_on_capped_postgres_store():
    """Hosted uses POSTMORTEM_STORE_BACKEND=postgres (HISTORY_READ_ROW_CAP=1000 on list_postmortems)."""
    from services.incident.pg_store import PostgresIncidentStore
    from services.postmortems import main as m
    rows = []
    for n in range(6000):
        base = infra_bff(f"x{n}") if n % 20 else drift(f"x{n}")
        rows.append(build_postmortem_draft_from_incident(base))
    cap = PostgresIncidentStore.HISTORY_READ_ROW_CAP
    class Capped:
        def list_postmortems(self): return rows[:cap]
        def list_all_postmortems(self): return list(rows)
    original = m.store
    m.store = Capped()
    try:
        b = client.get("/api/postmortems", params={"page_size": 200, "include_infrastructure": "true"}).json()
        assert b["total"] == 6000
        b = client.get("/api/postmortems", params={"page_size": 200}).json()
        assert b["total"] == 300 and b["hidden_infrastructure_count"] == 5700
    finally:
        m.store = original
