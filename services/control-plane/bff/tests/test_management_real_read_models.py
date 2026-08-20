"""Contract tests for real Management read models (PFG-MGMT-READ-MODELS-20260820).

Verifies endpoints:
- GET /bff/management/formula-jobs
- GET /bff/management/activity
- GET /bff/management/paper-telemetry
- GET /bff/management/postmortems
- GET /bff/management/postmortems/{postmortem_id}
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-read-models-001:operator,reviewer"}

_SAMPLE_FORMULA_JOBS = [
    {
        "job_id": "job-f1-001",
        "formula_id": "form-sharpe-v1",
        "formula_version": "1.0.0",
        "owner_id": "user-quant-01",
        "status": "completed",
        "submitted_at": "2026-08-20T10:00:00Z",
        "started_at": "2026-08-20T10:00:01Z",
        "finished_at": "2026-08-20T10:00:15Z",
        "metrics": {"ic": 0.05, "sharpe": 1.8},
        "chart_lineage": [{"step": "calc", "duration_ms": 14000}],
        "source_identity": "formula_job_executor",
        "freshness": "2026-08-20T10:00:15Z",
    }
]

_SAMPLE_ACTIVITIES = [
    {
        "event_id": "evt-act-001",
        "event_type": "formula.submitted",
        "aggregate_id": "form-sharpe-v1",
        "actor_id": "user-quant-01",
        "timestamp": "2026-08-20T09:59:50Z",
        "summary": "Formula form-sharpe-v1 submitted for evaluation",
        "details": {"version": "1.0.0"},
        "source_identity": "activity_audit_store",
        "freshness": "2026-08-20T09:59:50Z",
    }
]

_SAMPLE_PAPER_TELEMETRY = [
    {
        "strategy_id": "strat-momentum-01",
        "persona_id": "persona-alpha",
        "paper_ledger_id": "ledger-paper-01",
        "status": "active",
        "last_signal_at": "2026-08-20T11:00:00Z",
        "series": [
            {
                "timestamp": "2026-08-20T11:00:00Z",
                "equity": 105000.0,
                "drawdown_pct": 0.02,
                "open_positions": 3,
                "daily_pnl": 1200.0,
            }
        ],
        "metrics": {"total_trades": 45, "win_rate": 0.62},
        "source_identity": "paper_telemetry_store",
        "freshness": "2026-08-20T11:00:00Z",
    }
]

_SAMPLE_POSTMORTEMS = [
    {
        "postmortem_id": "pm-inc-001",
        "incident_id": "inc-20260819-01",
        "title": "Paper signal producer latency spike",
        "severity": "high",
        "status": "resolved",
        "created_at": "2026-08-19T14:00:00Z",
        "resolved_at": "2026-08-19T15:30:00Z",
        "root_cause": "Unbounded lifecycle outbox scanning",
        "impact_summary": "Paper worker CPU bound for 90 minutes",
        "action_items": [{"id": "act-1", "desc": "Implement cursor retention"}],
        "source_identity": "postmortem_store",
        "freshness": "2026-08-19T15:30:00Z",
    }
]


def _client_with_data(td: str) -> TestClient:
    store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
    store.get_formula_jobs_read_model = lambda status=None, formula_id=None: {
        "source": "service",
        "items": _SAMPLE_FORMULA_JOBS,
    }
    store.get_activity_read_model = lambda event_type=None, actor_id=None: {
        "source": "audit",
        "items": _SAMPLE_ACTIVITIES,
    }
    store.get_paper_telemetry_read_model = lambda strategy_id=None, persona_id=None: {
        "source": "service",
        "items": _SAMPLE_PAPER_TELEMETRY,
    }
    store.get_postmortems_read_model = lambda severity=None, status=None: {
        "source": "store",
        "items": _SAMPLE_POSTMORTEMS,
    }
    store.get_postmortem_detail_read_model = lambda postmortem_id: {
        "source": "store",
        "item": _SAMPLE_POSTMORTEMS[0] if postmortem_id == "pm-inc-001" else None,
    }
    bff_main.read_store = store
    return TestClient(bff_main.app)


def _client_unavailable(td: str) -> TestClient:
    store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )
    store.get_formula_jobs_read_model = lambda status=None, formula_id=None: {
        "source": "unavailable",
        "items": [],
    }
    store.get_activity_read_model = lambda event_type=None, actor_id=None: {
        "source": "unavailable",
        "items": [],
    }
    store.get_paper_telemetry_read_model = lambda strategy_id=None, persona_id=None: {
        "source": "unavailable",
        "items": [],
    }
    store.get_postmortems_read_model = lambda severity=None, status=None: {
        "source": "unavailable",
        "items": [],
    }
    store.get_postmortem_detail_read_model = lambda postmortem_id: {
        "source": "unavailable",
        "item": None,
    }
    bff_main.read_store = store
    return TestClient(bff_main.app)


def test_formula_jobs_endpoint():
    with tempfile.TemporaryDirectory() as td:
        client = _client_with_data(td)
        res = client.get("/bff/management/formula-jobs", headers=OPERATOR_HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["status"] == "ok"
        assert len(body["data"]["items"]) == 1
        job = body["data"]["items"][0]
        assert job["job_id"] == "job-f1-001"
        assert job["source_identity"] == "formula_job_executor"
        assert "freshness" in job


def test_activity_endpoint():
    with tempfile.TemporaryDirectory() as td:
        client = _client_with_data(td)
        res = client.get("/bff/management/activity", headers=OPERATOR_HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["status"] == "ok"
        assert len(body["data"]["items"]) == 1
        act = body["data"]["items"][0]
        assert act["event_id"] == "evt-act-001"
        assert act["source_identity"] == "activity_audit_store"


def test_paper_telemetry_endpoint():
    with tempfile.TemporaryDirectory() as td:
        client = _client_with_data(td)
        res = client.get("/bff/management/paper-telemetry", headers=OPERATOR_HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["status"] == "ok"
        assert len(body["data"]["items"]) == 1
        item = body["data"]["items"][0]
        assert item["strategy_id"] == "strat-momentum-01"
        assert len(item["series"]) == 1


def test_postmortems_endpoints():
    with tempfile.TemporaryDirectory() as td:
        client = _client_with_data(td)
        # List
        res = client.get("/bff/management/postmortems", headers=OPERATOR_HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["status"] == "ok"
        assert len(body["data"]["items"]) == 1
        pm = body["data"]["items"][0]
        assert pm["postmortem_id"] == "pm-inc-001"

        # Detail - found
        res_detail = client.get("/bff/management/postmortems/pm-inc-001", headers=OPERATOR_HEADERS)
        assert res_detail.status_code == 200
        detail_body = res_detail.json()
        assert detail_body["data"]["postmortem_id"] == "pm-inc-001"

        # Detail - not found
        res_nf = client.get("/bff/management/postmortems/non-existent", headers=OPERATOR_HEADERS)
        assert res_nf.status_code == 404


def test_unavailable_degraded_behavior():
    with tempfile.TemporaryDirectory() as td:
        client = _client_unavailable(td)
        for endpoint in [
            "/bff/management/formula-jobs",
            "/bff/management/activity",
            "/bff/management/paper-telemetry",
            "/bff/management/postmortems",
        ]:
            res = client.get(endpoint, headers=OPERATOR_HEADERS)
            assert res.status_code == 200
            body = res.json()
            assert body["data"]["status"] == "unavailable"
            assert body["data"]["items"] == []
            assert body["meta"]["status"] == "unavailable"
            assert "degradation" in body["meta"]
