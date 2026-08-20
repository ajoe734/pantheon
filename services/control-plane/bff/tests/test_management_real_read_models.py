"""Contract tests for real Management read models (PFG-MGMT-READ-MODELS-20260820).

Verifies endpoints:
- GET /bff/management/formula-jobs
- GET /bff/management/activity
- GET /bff/management/paper-telemetry
- GET /bff/management/postmortems
- GET /bff/management/postmortems/{postmortem_id}
"""
from __future__ import annotations

import json
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


def test_service_backed_adapter_readback():
    with tempfile.TemporaryDirectory() as td:
        postmortems_dir = os.path.join(td, "postmortems")
        os.makedirs(postmortems_dir, exist_ok=True)
        pm_path = os.path.join(postmortems_dir, "postmortems.json")
        canonical_pm = {
            "postmortem_id": "pm-canonical-101",
            "title": "Canonical Incident Postmortem",
            "status": "published",
            "created_at": "2026-08-20T12:00:00Z",
            "incident_id": "inc-101",
            "binding_id": "bind-101",
            "deployment_stage": "canary",
            "deployment_plan_id": "plan-101",
            "capital_pool_id": "pool-101",
            "persona_capital_binding_id": "pcb-101",
            "artifact_id": "art-101",
            "artifact_version": "1.0.0",
            "runtime_id": "run-101",
            "trace_id": "tr-101",
            "root_cause": "Buffer overflow in streaming ingest",
            "incident_evidence_summary": "High latency across ingest pipelines",
            "action_items": ["Add backpressure buffer", "Rate limit requests"],
        }
        with open(pm_path, "w", encoding="utf-8") as f:
            json.dump([canonical_pm], f)

        # Test ReadSurfaceStore reading from file via ServiceBackedReadAdapter
        os.environ["POSTMORTEMS_DATA_DIR"] = postmortems_dir
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            res = store.get_postmortems_read_model()
            assert res["source"] == "store"
            assert len(res["items"]) == 1
            item = res["items"][0]
            assert item["postmortem_id"] == "pm-canonical-101"
            assert item["impact_summary"] == "High latency across ingest pipelines"
            assert item["severity"] == "medium"
            assert item["deployment_stage"] == "canary"
            assert len(item["action_items"]) == 2
            assert item["action_items"][0] == {"id": "act-1", "desc": "Add backpressure buffer"}
            assert item["source_identity"] == "postmortem_store"
            assert item["freshness"] == "2026-08-20T12:00:00Z"
        finally:
            os.environ.pop("POSTMORTEMS_DATA_DIR", None)


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


def test_file_backed_readback_all_endpoints():
    with tempfile.TemporaryDirectory() as td:
        formula_dir = os.path.join(td, "formula")
        activity_dir = os.path.join(td, "activity")
        paper_dir = os.path.join(td, "paper")
        postmortems_dir = os.path.join(td, "postmortems")
        for d in [formula_dir, activity_dir, paper_dir, postmortems_dir]:
            os.makedirs(d, exist_ok=True)

        with open(os.path.join(formula_dir, "formula_jobs.json"), "w", encoding="utf-8") as f:
            json.dump(_SAMPLE_FORMULA_JOBS, f)

        with open(os.path.join(activity_dir, "activity_audit.json"), "w", encoding="utf-8") as f:
            json.dump(_SAMPLE_ACTIVITIES, f)

        with open(os.path.join(paper_dir, "paper_telemetry.json"), "w", encoding="utf-8") as f:
            json.dump(_SAMPLE_PAPER_TELEMETRY, f)

        with open(os.path.join(postmortems_dir, "postmortems.json"), "w", encoding="utf-8") as f:
            json.dump(_SAMPLE_POSTMORTEMS, f)

        old_env = os.environ.copy()
        os.environ["PANTHEON_BFF_FORMULA_JOBS_STORE"] = os.path.join(formula_dir, "formula_jobs.json")
        os.environ["PANTHEON_BFF_ACTIVITY_AUDIT_STORE"] = os.path.join(activity_dir, "activity_audit.json")
        os.environ["PANTHEON_BFF_PAPER_TELEMETRY_STORE"] = os.path.join(paper_dir, "paper_telemetry.json")
        os.environ["PANTHEON_BFF_POSTMORTEM_STORE"] = os.path.join(postmortems_dir, "postmortems.json")

        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            bff_main.read_store = store
            client = TestClient(bff_main.app)

            # Test formula jobs file readback
            r_fj = client.get("/bff/management/formula-jobs", headers=OPERATOR_HEADERS)
            assert r_fj.status_code == 200
            data_fj = r_fj.json()["data"]
            assert data_fj["status"] == "ok"
            assert len(data_fj["items"]) == 1
            assert data_fj["items"][0]["job_id"] == "job-f1-001"

            # Test activity file readback
            r_act = client.get("/bff/management/activity", headers=OPERATOR_HEADERS)
            assert r_act.status_code == 200
            data_act = r_act.json()["data"]
            assert data_act["status"] == "ok"
            assert len(data_act["items"]) == 1
            assert data_act["items"][0]["event_id"] == "evt-act-001"

            # Test paper telemetry file readback
            r_pt = client.get("/bff/management/paper-telemetry", headers=OPERATOR_HEADERS)
            assert r_pt.status_code == 200
            data_pt = r_pt.json()["data"]
            assert data_pt["status"] == "ok"
            assert len(data_pt["items"]) == 1
            assert data_pt["items"][0]["strategy_id"] == "strat-momentum-01"

            # Test postmortems file readback
            r_pm = client.get("/bff/management/postmortems", headers=OPERATOR_HEADERS)
            assert r_pm.status_code == 200
            data_pm = r_pm.json()["data"]
            assert data_pm["status"] == "ok"
            assert len(data_pm["items"]) == 1
            assert data_pm["items"][0]["postmortem_id"] == "pm-inc-001"
        finally:
            os.environ.clear()
            os.environ.update(old_env)
