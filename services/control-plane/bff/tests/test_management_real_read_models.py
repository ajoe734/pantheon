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


def test_readback_freshness_and_restart():
    with tempfile.TemporaryDirectory() as td:
        formula_dir = os.path.join(td, "formula")
        os.makedirs(formula_dir, exist_ok=True)
        store_file = os.path.join(formula_dir, "formula_jobs.json")

        initial_jobs = [
            {
                "job_id": "job-init-01",
                "formula_id": "form-v1",
                "status": "completed",
                "submitted_at": "2026-08-21T00:00:00Z",
            }
        ]
        with open(store_file, "w", encoding="utf-8") as f:
            json.dump(initial_jobs, f)

        old_env = os.environ.copy()
        os.environ["PANTHEON_BFF_FORMULA_JOBS_STORE"] = store_file
        try:
            # 1. First initialization/readback
            store1 = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=False)
            res1 = store1.get_formula_jobs_read_model()
            assert res1["source"] == "service"
            assert len(res1["items"]) == 1
            assert res1["items"][0]["job_id"] == "job-init-01"
            assert res1["items"][0]["freshness"] == "2026-08-21T00:00:00Z"

            # 2. Update store file on disk (simulate producer update)
            updated_jobs = [
                *initial_jobs,
                {
                    "job_id": "job-init-02",
                    "formula_id": "form-v2",
                    "status": "running",
                    "submitted_at": "2026-08-21T01:00:00Z",
                },
            ]
            with open(store_file, "w", encoding="utf-8") as f:
                json.dump(updated_jobs, f)

            # 3. Process restart / new ReadSurfaceStore instance reading updated store
            store2 = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=False)
            res2 = store2.get_formula_jobs_read_model()
            assert res2["source"] == "service"
            assert len(res2["items"]) == 2
            assert res2["items"][0]["job_id"] == "job-init-02"
            assert res2["items"][0]["freshness"] == "2026-08-21T01:00:00Z"
            assert res2["items"][1]["job_id"] == "job-init-01"
        finally:
            os.environ.clear()
            os.environ.update(old_env)


def test_canonical_owners_projection():
    """Verify that canonical jobs, audit events, telemetry events, and runtime bindings are projected into read models."""
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        # Mock canonical datasets on store
        store._service.list_records = lambda dataset, *args, **kwargs: {
            "jobs": (True, [{
                "job_id": "job-canon-01",
                "formula_id": "formula-alpha",
                "status": "completed",
                "created_at": "2026-08-21T02:00:00Z",
                "metrics": {"ic": 0.08},
            }]),
            "formula_jobs": (False, []),
            "activity_audit": (False, []),
            "governance_audit_events": (True, [{
                "entry_id": "gov-aud-01",
                "action_type": "policy.override",
                "target_id": "pol-01",
                "actor": "admin-01",
                "timestamp": "2026-08-21T02:01:00Z",
                "summary": "Override policy threshold",
            }]),
            "paper_telemetry": (False, []),
            "postmortems": (True, [{
                "id": "pm-canon-01",
                "incident_id": "inc-01",
                "title": "Ingest lag",
                "incident_evidence_summary": "High lag",
                "action_items": ["Scale worker"],
            }]),
            "telemetry_events": (True, [{
                "id": "tel-01",
                "type": "telemetry.runtime",
                "runtime_id": "strat-canon-01",
                "timestamp": "2026-08-21T02:05:00Z",
                "metrics": {
                    "equity": 102000.0,
                    "drawdown_pct": 0.01,
                    "open_positions": 2,
                    "daily_pnl": 500.0,
                },
            }]),
        }.get(dataset, (False, []))
        store._service.cached_source = lambda dataset: "canonical"

        store.list_runtime_bindings = lambda **kwargs: [{
            "strategy_id": "strat-canon-01",
            "persona_id": "persona-1",
            "paper_ledger_id": "ledger-paper-canon-01",
            "status": "active",
            "deployment_stage": "paper",
        }]

        # 1. Test formula-jobs projecting from canonical jobs
        fj_res = store.get_formula_jobs_read_model()
        assert fj_res["source"] == "service"
        assert len(fj_res["items"]) == 1
        assert fj_res["items"][0]["job_id"] == "job-canon-01"
        assert fj_res["items"][0]["formula_id"] == "formula-alpha"

        # 2. Test activity projecting from canonical governance audit (using entry_id) and telemetry
        act_res = store.get_activity_read_model()
        assert act_res["source"] == "audit"
        assert len(act_res["items"]) >= 2
        eids = [x["event_id"] for x in act_res["items"]]
        assert "gov-aud-01" in eids
        assert "tel-01" in eids

        # 3. Test paper telemetry projecting from runtime bindings + telemetry events
        pt_res = store.get_paper_telemetry_read_model()
        assert pt_res["source"] == "service"
        assert len(pt_res["items"]) == 1
        assert pt_res["items"][0]["strategy_id"] == "strat-canon-01"
        assert len(pt_res["items"][0]["series"]) == 1
        assert pt_res["items"][0]["series"][0]["equity"] == 102000.0

        # 4. Test postmortem projecting from canonical postmortems
        pm_res = store.get_postmortems_read_model()
        assert pm_res["source"] == "store"
        assert len(pm_res["items"]) == 1
        assert pm_res["items"][0]["postmortem_id"] == "pm-canon-01"
        assert pm_res["items"][0]["impact_summary"] == "High lag"
        assert pm_res["items"][0]["action_items"][0] == {"id": "act-1", "desc": "Scale worker"}


def test_activity_canonical_governance_audit_entry_id_preservation():
    """Verify that canonical governance audit records with entry_id are fully preserved without dropping rows."""
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        canonical_audit_records = [
            {
                "entry_id": "audit-pack-a-strategy-approved",
                "action_type": "ApproveDecision",
                "target_type": "ApprovalDecision",
                "target_id": "dec-001",
                "actor": "operator-01",
                "timestamp": "2026-08-20T08:00:00Z",
                "outcome": "success",
                "audit_context": {"reason": "Met all criteria"},
            },
            {
                "entry_id": "audit-pack-a-persona-policy",
                "action_type": "UpdatePolicy",
                "target_type": "Policy",
                "target_id": "pol-002",
                "actor": "governance-lead",
                "timestamp": "2026-08-20T08:30:00Z",
                "outcome": "success",
                "audit_context": {"reason": "Risk threshold tightening"},
            },
        ]
        store._service.list_records = lambda dataset, *args, **kwargs: {
            "governance_audit_events": (True, canonical_audit_records),
            "activity_audit": (False, []),
        }.get(dataset, (False, []))
        store._service.cached_source = lambda dataset: "canonical"

        act_res = store.get_activity_read_model()
        assert act_res["source"] == "audit"
        assert len(act_res["items"]) == 2
        assert act_res["items"][0]["event_id"] == "audit-pack-a-persona-policy"
        assert act_res["items"][0]["entry_id"] == "audit-pack-a-persona-policy"
        assert act_res["items"][0]["actor_id"] == "governance-lead"
        assert act_res["items"][0]["event_type"] == "UpdatePolicy"
        assert act_res["items"][0]["source_identity"] == "governance_audit_store"
        assert act_res["items"][1]["event_id"] == "audit-pack-a-strategy-approved"
        assert act_res["items"][1]["entry_id"] == "audit-pack-a-strategy-approved"


def test_activity_source_derivation_telemetry_only_vs_audit():
    """Verify that when only telemetry events exist, source is telemetry; when audit exists, source is audit."""
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )

        # 1. Telemetry only
        store._service.list_records = lambda dataset, *args, **kwargs: {
            "governance_audit_events": (False, []),
            "activity_audit": (False, []),
            "telemetry_events": (True, [{
                "id": "tel-only-01",
                "type": "telemetry.runtime",
                "runtime_id": "strat-01",
                "timestamp": "2026-08-21T01:00:00Z",
                "metrics": {"equity": 100000.0},
            }]),
        }.get(dataset, (False, []))
        store._service.cached_source = lambda dataset: "canonical"

        res_tel_only = store.get_activity_read_model()
        assert res_tel_only["source"] == "telemetry"
        assert len(res_tel_only["items"]) == 1
        assert res_tel_only["items"][0]["event_id"] == "tel-only-01"
        assert res_tel_only["surfaces"]["telemetry_events"]["status"] == "ok"
        assert res_tel_only["surfaces"]["governance_audit"]["status"] == "unavailable"

        # 2. Both telemetry and audit
        store._service.list_records = lambda dataset, *args, **kwargs: {
            "governance_audit_events": (True, [{
                "entry_id": "gov-01",
                "action_type": "Approve",
                "timestamp": "2026-08-21T01:05:00Z",
                "actor": "admin",
            }]),
            "activity_audit": (False, []),
            "telemetry_events": (True, [{
                "id": "tel-01",
                "type": "telemetry.runtime",
                "timestamp": "2026-08-21T01:00:00Z",
            }]),
        }.get(dataset, (False, []))

        res_both = store.get_activity_read_model()
        assert res_both["source"] == "audit"
        assert len(res_both["items"]) == 2
        assert res_both["surfaces"]["telemetry_events"]["status"] == "ok"
        assert res_both["surfaces"]["governance_audit"]["status"] == "ok"


def test_governance_audit_file_backed_readback():
    """Verify non-mocked on-disk governance audit file readback through FastAPI endpoint."""
    with tempfile.TemporaryDirectory() as td:
        gov_dir = os.path.join(td, "governance")
        os.makedirs(gov_dir, exist_ok=True)
        gov_file = os.path.join(gov_dir, "governance_audit_events.json")

        sample_gov_records = [
            {
                "entry_id": "audit-canon-file-01",
                "action_type": "ApproveDeployment",
                "target_type": "DeploymentPlan",
                "target_id": "dp-20260821-01",
                "actor": "op-reviewer-01",
                "timestamp": "2026-08-21T02:30:00Z",
                "outcome": "success",
                "audit_context": {"reason": "Passed risk thresholds"},
            }
        ]
        with open(gov_file, "w", encoding="utf-8") as f:
            json.dump(sample_gov_records, f)

        old_env = os.environ.copy()
        os.environ["PANTHEON_BFF_GOVERNANCE_AUDIT_STORE"] = gov_file
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            bff_main.read_store = store
            client = TestClient(bff_main.app)

            res = client.get("/bff/management/activity", headers=OPERATOR_HEADERS)
            assert res.status_code == 200
            body = res.json()
            data = body["data"]
            assert data["status"] == "ok"
            assert data["source"] == "audit"
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["event_id"] == "audit-canon-file-01"
            assert item["entry_id"] == "audit-canon-file-01"
            assert item["event_type"] == "ApproveDeployment"
            assert item["actor_id"] == "op-reviewer-01"
            assert item["source_identity"] == "governance_audit_store"
            assert body["meta"]["surfaces"]["governance_audit"]["status"] == "ok"
        finally:
            os.environ.clear()
            os.environ.update(old_env)


def test_activity_empty_degraded_when_stores_empty():
    """Verify that when store file is readable but empty ([]), endpoint returns degraded state with source=audit."""
    with tempfile.TemporaryDirectory() as td:
        gov_dir = os.path.join(td, "governance")
        os.makedirs(gov_dir, exist_ok=True)
        gov_file = os.path.join(gov_dir, "governance_audit_events.json")

        with open(gov_file, "w", encoding="utf-8") as f:
            json.dump([], f)

        old_env = os.environ.copy()
        os.environ["PANTHEON_BFF_GOVERNANCE_AUDIT_STORE"] = gov_file
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            bff_main.read_store = store
            client = TestClient(bff_main.app)

            res = client.get("/bff/management/activity", headers=OPERATOR_HEADERS)
            assert res.status_code == 200
            body = res.json()
            data = body["data"]
            assert data["status"] == "degraded"
            assert data["source"] == "audit"
            assert data["items"] == []
            assert body["meta"]["status"] == "degraded"
        finally:
            os.environ.clear()
            os.environ.update(old_env)
