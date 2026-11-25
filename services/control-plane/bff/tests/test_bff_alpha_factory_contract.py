"""Contract tests for GET /bff/alpha-factory (BFF-AF-001 / ALPHA-FACTORY-REPLICATION-READ-20261104).

Verifies:
1. GET /bff/alpha-factory builds its cards over HTTP from
   GET /api/research-orchestrator/alpha-replication/entries with verified caller auth and tenant.
2. Each card preserves strategy_id, strategy_spec_id, admission status, queue status,
   attempt_count, experiment_task_id, experiment_run_ids, authority_task_id,
   last_revalidation_at, approver, and approved_at without inventing metrics/Sharpe/risk,
   and keeps missing values as null.
3. Lanes are discovered, scaffolded, and replicated: completed queue entries land in
   replicated; non-completed or unqueued admissions land in scaffolded; discovered is
   empty only when the owners answered with none (BFF-ALPHA-FACTORY-PIPELINE-TRUTH-20261125).
4. Owner read failure or unconfigured PANTHEON_RESEARCH_ORCHESTRATOR_API_URL returns
   meta.surfaces.alpha_factory.status == 'unavailable' with items == []; a healthy owner
   with no records returns status == 'ok' with empty lanes.
5. Two-tenant isolation holds across both the owner route and the BFF route with real
   auth middleware active.
6. Dead alpha_factory_cards / list_alpha_factory_cards readers are removed from the
   read-surface ports.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.console_gap.alpha_factory import create_alpha_factory_router
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.ports.operations_consultation import (
    CompositeOperationsConsultationPort,
    DomainWorkflowCatalogPort,
    InMemoryOperationsConsultationPort,
    WorkflowHookCatalogReaderPort,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXED_NOW = "2026-11-04T00:00:00Z"
JWT_SECRET = "bff-alpha-factory-contract-secret-0123456789abcdef"

HOSTED_TW_OFFICIAL_SPEC_ID = (
    "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-da7cb8297afb"
)
HOSTED_TW_OFFICIAL_STRATEGY_ID = (
    "strat-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-60f9c8a78715"
)
VALID_CHECKSUM = "sha256:" + ("a" * 64)


def _hs256_bearer(
    tenant_id: str,
    *,
    roles: Optional[List[str]] = None,
    sub: str = "pantheon-dev-operator",
    secret: str = JWT_SECRET,
) -> str:
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    eff_roles = roles if roles is not None else ["operator", "reviewer"]
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64(
        json.dumps(
            {
                "sub": sub,
                "roles": eff_roles,
                "tenant_id": tenant_id,
            }
        ).encode("utf-8")
    )
    sig = _b64(
        hmac.new(
            secret.encode("utf-8"),
            f"{header}.{payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    return f"Bearer {header}.{payload}.{sig}"


def _auth_headers(tenant_id: str = "tenant-dev", *, roles: Optional[List[str]] = None) -> Dict[str, str]:
    return {
        "Authorization": _hs256_bearer(tenant_id, roles=roles),
        "X-Tenant-Id": tenant_id,
    }


def _hosted_owner_entries() -> List[Dict[str, Any]]:
    return [
        {
            "admission_id": "adm-5dc7be51a48996e4",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2330-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "created_at": "2026-08-14T01:05:00Z",
            "admission_status": "admitted",
            "status": "admitted",
            "queue_status": "completed",
            "enqueued_at": "2026-08-14T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "attempt_count": 1,
            "max_attempts": 1,
            "experiment_task_id": "etask-alpha-6ef7a5a6d3a4ae4b",
            "experiment_run_ids": ["run-exp-2330-001"],
            "authority_task_id": "rtask-20261015-002",
            "authority_run_ids": ["run-auth-2330-001"],
            "last_revalidation_at": "2026-10-15T08:30:00Z",
            "last_error": None,
        },
        {
            "admission_id": "adm-pending-2317",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2317:pending-spec",
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2317:pending-strat",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2317-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-15T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "created_at": "2026-08-15T01:05:00Z",
            "admission_status": "admitted",
            "status": "admitted",
            "queue_status": "pending",
            "enqueued_at": "2026-08-15T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "attempt_count": 0,
            "max_attempts": 3,
            "experiment_task_id": None,
            "experiment_run_ids": [],
            "authority_task_id": None,
            "authority_run_ids": None,
            "last_revalidation_at": None,
            "last_error": None,
        },
        {
            "admission_id": "adm-failed-2454",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2454:failed-spec",
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2454:failed-strat",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2454-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-16T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "created_at": "2026-08-16T01:05:00Z",
            "admission_status": "admitted",
            "status": "admitted",
            "queue_status": "failed",
            "enqueued_at": "2026-08-16T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "attempt_count": 2,
            "max_attempts": 2,
            "experiment_task_id": "etask-alpha-failed-2454",
            "experiment_run_ids": ["run-exp-2454-001", "run-exp-2454-002"],
            "authority_task_id": None,
            "authority_run_ids": None,
            "last_revalidation_at": None,
            "last_error": "worker timeout",
        },
        {
            "admission_id": "adm-unqueued-2308",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2308:unqueued-spec",
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2308:unqueued-strat",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2308-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-17T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "created_at": "2026-08-17T01:05:00Z",
            "admission_status": "admitted",
            "status": "admitted",
            "queue_status": None,
            "enqueued_at": None,
            "enqueued_by": None,
            "attempt_count": None,
            "max_attempts": None,
            "experiment_task_id": None,
            "experiment_run_ids": None,
            "authority_task_id": None,
            "authority_run_ids": None,
            "last_revalidation_at": None,
            "last_error": None,
        },
    ]


def _gate_run(run_id: str, *, passed: bool = True, tenant: str = "tenant-dev") -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "tenant_id": tenant,
        "status": "completed",
        "metadata": {"replication_gate": {"passed": passed, "status": "passed" if passed else "failed"}},
    }


def _owner_router(
    entries: List[Dict[str, Any]],
    *,
    runs: Optional[List[Dict[str, Any]]] = None,
    demands: Optional[List[Dict[str, Any]]] = None,
    seeds: Optional[List[Dict[str, Any]]] = None,
    records: Optional[List[Dict[str, Any]]] = None,
    templates: Optional[List[Dict[str, Any]]] = None,
    fail: Tuple[str, ...] = (),
    gate_all_completed: bool = False,
    calls: Optional[List[str]] = None,
):
    """Owner answers keyed by route; ``gate_all_completed`` gives every completed entry a passed gate run."""
    eff_runs = list(runs or [])
    if gate_all_completed:
        for entry in entries:
            if entry.get("queue_status") == "completed":
                for rid in entry.get("experiment_run_ids") or []:
                    eff_runs.append(_gate_run(rid, tenant=entry.get("tenant_id") or "tenant-dev"))

    def fake(url: str, **_kw: Any) -> Tuple[int, Any]:
        if calls is not None:
            calls.append(url)
        for marker in fail:
            if marker in url:
                return 503, None
        if "/alpha-replication/entries" in url:
            return 200, entries
        if "/alpha-templates/evaluations" in url:
            return 200, {"items": templates or []}
        if "/persona-demands" in url:
            return 200, {"demands": demands or [], "count": len(demands or [])}
        if "/research-orchestrator/runs" in url:
            return 200, eff_runs
        if "/strategy-seeds" in url:
            return 200, {"items": seeds or [], "count": len(seeds or [])}
        if "/source-records" in url:
            return 200, {"source_records": records or []}
        return 404, None

    return fake


def _make_bff_client(
    monkeypatch,
    *,
    http_get: Optional[ Any ] = None,
    orchestrator_url: Optional[str] = "http://research-orchestrator.local",
    read_strategy_state: Optional[Any] = None,
) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    if orchestrator_url is not None:
        monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", orchestrator_url)
    else:
        monkeypatch.delenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", raising=False)

    app = FastAPI(middleware=[Middleware(OwnerReadContextMiddleware)])
    router = create_alpha_factory_router(
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        utc_now=lambda: FIXED_NOW,
        http_get=http_get,
        read_strategy_state=read_strategy_state,
    )
    app.include_router(router)
    return TestClient(app)


def test_alpha_factory_builds_cards_from_owner_route_with_hosted_shapes(monkeypatch) -> None:
    calls: List[str] = []
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.local")
    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router(_hosted_owner_entries(), gate_all_completed=True, calls=calls),
    )
    resp = client.get("/bff/alpha-factory", headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    # entries, templates, demands and runs: four owner reads; never source-records or strategy-seeds
    assert len(calls) == 4
    assert not any("/source-records" in c or "/strategy-seeds" in c for c in calls)
    assert calls[0] == (
        "http://research-orchestrator.local/api/research-orchestrator/alpha-replication/entries"
    )

    payload = resp.json()
    assert payload["meta"]["snapshot_at"] == FIXED_NOW
    assert payload["meta"]["surfaces"]["alpha_factory"] == {
        "status": "ok",
        "source": "research_orchestrator",
    }

    data = payload["data"]
    assert data["id"] == "alpha-factory"
    assert "reason" not in data and "discovered_reason" not in data
    assert data["discovered"] == []
    assert data["document_checked"] == []

    lanes = {lane["id"]: lane for lane in data["lanes"]}
    assert list(lanes.keys()) == ["discovered", "scaffolded", "replicated"]
    assert "reason" not in lanes["discovered"]
    assert lanes["discovered"]["items"] == []

    replicated_cards = lanes["replicated"]["items"]
    assert len(replicated_cards) == 1
    rep = replicated_cards[0]
    assert rep["id"] == "adm-5dc7be51a48996e4"
    assert rep["lane"] == "replicated"
    assert rep["strategy_id"] == HOSTED_TW_OFFICIAL_STRATEGY_ID
    assert rep["strategy_spec_id"] == HOSTED_TW_OFFICIAL_SPEC_ID
    assert rep["status"] == "admitted"
    assert rep["admission_status"] == "admitted"
    assert rep["queue_status"] == "completed"
    assert rep["attempt_count"] == 1
    assert rep["experiment_task_id"] == "etask-alpha-6ef7a5a6d3a4ae4b"
    assert rep["experiment_run_ids"] == ["run-exp-2330-001"]
    assert rep["authority_task_id"] == "rtask-20261015-002"
    assert rep["authority_run_ids"] == ["run-auth-2330-001"]
    assert rep["last_revalidation_at"] == "2026-10-15T08:30:00Z"
    assert rep["approver"] == "pantheon-dev-approver"
    assert rep["approved_at"] == "2026-08-14T01:00:00Z"
    for invented_field in ("sharpe", "sharpe_ratio", "risk", "win_rate"):
        assert invented_field not in rep
    # metrics is present only as a pass-through: null when the owner supplied none.
    assert rep["metrics"] is None

    scaffolded_cards = lanes["scaffolded"]["items"]
    assert len(scaffolded_cards) == 3
    scaffolded_by_id = {c["id"]: c for c in scaffolded_cards}
    assert set(scaffolded_by_id.keys()) == {
        "adm-pending-2317",
        "adm-failed-2454",
        "adm-unqueued-2308",
    }

    unqueued = scaffolded_by_id["adm-unqueued-2308"]
    assert unqueued["lane"] == "scaffolded"
    assert unqueued["status"] == "admitted"
    assert unqueued["admission_status"] == "admitted"
    assert unqueued["queue_status"] is None
    assert unqueued["attempt_count"] is None
    assert unqueued["experiment_task_id"] is None
    assert unqueued["experiment_run_ids"] is None
    assert unqueued["authority_task_id"] is None
    assert unqueued["last_revalidation_at"] is None


def test_alpha_factory_lane_filter_is_honoured(monkeypatch) -> None:
    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router(_hosted_owner_entries(), gate_all_completed=True),
    )

    rep_resp = client.get("/bff/alpha-factory?lane=replicated", headers=_auth_headers("tenant-dev"))
    assert rep_resp.status_code == 200
    rep_body = rep_resp.json()
    assert rep_body["meta"]["filters"]["lane"] == "replicated"
    assert [c["id"] for c in rep_body["items"]] == ["adm-5dc7be51a48996e4"]
    assert rep_body["page_info"]["total"] == 1

    scaf_resp = client.get("/bff/alpha-factory?lane=scaffolded", headers=_auth_headers("tenant-dev"))
    assert scaf_resp.status_code == 200
    scaf_body = scaf_resp.json()
    assert scaf_body["meta"]["filters"]["lane"] == "scaffolded"
    assert len(scaf_body["items"]) == 3
    assert all(c["lane"] == "scaffolded" for c in scaf_body["items"])

    disc_resp = client.get("/bff/alpha-factory?lane=discovered", headers=_auth_headers("tenant-dev"))
    assert disc_resp.status_code == 200
    disc_body = disc_resp.json()
    assert disc_body["items"] == []
    assert "reason" not in disc_body["data"]


def test_alpha_factory_unconfigured_url_returns_unavailable_envelope(monkeypatch) -> None:
    client = _make_bff_client(monkeypatch, orchestrator_url=None)
    resp = client.get("/bff/alpha-factory", headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["items"] == []
    assert payload["data"]["items"] == []
    surface = payload["meta"]["surfaces"]["alpha_factory"]
    assert surface["status"] == "unavailable"
    assert surface["source"] == "research_orchestrator"
    assert surface["staleness"]["served_from"] == "unverifiable"


def test_alpha_factory_owner_http_failure_returns_unavailable_envelope(monkeypatch) -> None:
    client = _make_bff_client(
        monkeypatch,
        http_get=lambda url, **kw: (503, {"detail": "orchestrator down"}),
    )
    resp = client.get("/bff/alpha-factory", headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["items"] == []
    assert payload["data"]["items"] == []
    assert payload["page_info"]["total"] == 0
    surface = payload["meta"]["surfaces"]["alpha_factory"]
    assert surface["status"] == "unavailable"
    assert surface["source"] == "research_orchestrator"


def test_alpha_factory_healthy_empty_owner_returns_ok_with_empty_lanes(monkeypatch) -> None:
    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router([]),
    )
    resp = client.get("/bff/alpha-factory", headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["items"] == []
    assert payload["data"]["items"] == []
    assert payload["data"]["discovered"] == []
    assert payload["data"]["scaffolded"] == []
    assert payload["data"]["replicated"] == []
    assert "reason" not in payload["data"]
    surface = payload["meta"]["surfaces"]["alpha_factory"]
    assert surface == {"status": "ok", "source": "research_orchestrator"}


def test_alpha_factory_two_tenant_isolation_across_owner_and_bff_with_real_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", JWT_SECRET)

    research_dir = tmp_path / "research-orchestrator"
    alpha_dir = tmp_path / "alpha-replication"
    env = {
        "RESEARCH_ORCHESTRATOR_DATA_DIR": str(research_dir),
        "ALPHA_REPLICATION_DATA_DIR": str(alpha_dir),
        "PANTHEON_RESEARCH_AUTH_MODE": "strict",
        "PANTHEON_RESEARCH_JWT_SECRET": JWT_SECRET,
    }
    module_name = "research_main_bff_tenant_isolation"
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location(
            module_name, REPO_ROOT / "services" / "research" / "main.py"
        )
        assert spec and spec.loader
        owner_mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = owner_mod
        spec.loader.exec_module(owner_mod)

    alpha_dir.mkdir(parents=True, exist_ok=True)
    other_spec_id = "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2303:tenant-other-spec"
    admissions = {
        f"tenant-dev:{HOSTED_TW_OFFICIAL_SPEC_ID}": {
            "admission_id": "adm-5dc7be51a48996e4",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-dev-2330",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "status": "admitted",
            "created_at": "2026-08-14T01:05:00Z",
        },
        f"tenant-other:{other_spec_id}": {
            "admission_id": "adm-other-2303",
            "tenant_id": "tenant-other",
            "strategy_spec_id": other_spec_id,
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2303:tenant-other-strat",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-other-2303",
            "approver": "other-approver",
            "approved_at": "2026-08-14T02:00:00Z",
            "requested_by": "other-operator",
            "review_ref": None,
            "mode": "initial",
            "status": "admitted",
            "created_at": "2026-08-14T02:05:00Z",
        },
    }
    queue_entries = {
        f"tenant-dev:{HOSTED_TW_OFFICIAL_SPEC_ID}": {
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "tenant_id": "tenant-dev",
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-dev-2330",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
            "enqueued_at": "2026-08-14T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "status": "completed",
            "attempt_count": 1,
            "max_attempts": 1,
            "experiment_task_id": "etask-alpha-6ef7a5a6d3a4ae4b",
            "experiment_run_ids": ["run-exp-2330-001"],
            "authority_task_id": "rtask-20261015-002",
            "authority_run_ids": ["run-auth-2330-001"],
            "last_revalidation_at": "2026-10-15T08:30:00Z",
        },
        f"tenant-other:{other_spec_id}": {
            "strategy_spec_id": other_spec_id,
            "tenant_id": "tenant-other",
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2303:tenant-other-strat",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-other-2303",
            "approver": "other-approver",
            "approved_at": "2026-08-14T02:00:00Z",
            "enqueued_at": "2026-08-14T02:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 1,
            "experiment_task_id": None,
            "experiment_run_ids": [],
            "authority_task_id": None,
            "authority_run_ids": None,
            "last_revalidation_at": None,
        },
    }
    (alpha_dir / "admissions.json").write_text(json.dumps(admissions), encoding="utf-8")
    (alpha_dir / "queue.json").write_text(json.dumps(queue_entries), encoding="utf-8")

    owner_client = TestClient(owner_mod.app)

    def bridge_http_request(
        url: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
        timeout: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> Tuple[int, Dict[str, str], Any]:
        del payload, mfa_token, timeout
        path = url.replace("http://research-orchestrator.local", "")
        req_headers: Dict[str, str] = {}
        if auth_token:
            req_headers["Authorization"] = (
                auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
            )
        if tenant_id:
            req_headers["X-Tenant-Id"] = tenant_id
        resp = owner_client.request(method, path, headers=req_headers)
        return resp.status_code, dict(resp.headers), resp.json()

    monkeypatch.setattr(
        "services.control_plane.bff.console_gap.alpha_factory.http_request_json_with_headers",
        bridge_http_request,
    )

    bff_client = _make_bff_client(monkeypatch)

    # 1. Owner route directly isolates tenant-dev vs tenant-other
    owner_dev = owner_client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_auth_headers("tenant-dev"),
    )
    assert owner_dev.status_code == 200
    assert [e["strategy_spec_id"] for e in owner_dev.json()] == [HOSTED_TW_OFFICIAL_SPEC_ID]

    owner_other = owner_client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_auth_headers("tenant-other"),
    )
    assert owner_other.status_code == 200
    assert [e["strategy_spec_id"] for e in owner_other.json()] == [other_spec_id]

    # 2. BFF route over HTTP isolates tenant-dev vs tenant-other
    bff_dev = bff_client.get("/bff/alpha-factory", headers=_auth_headers("tenant-dev"))
    assert bff_dev.status_code == 200
    bff_dev_body = bff_dev.json()
    assert [c["strategy_spec_id"] for c in bff_dev_body["data"]["document_checked"]] == [HOSTED_TW_OFFICIAL_SPEC_ID]
    # the owner records no passing gate run for this admission: a document check, not a pass
    assert bff_dev_body["data"]["replicated"] == []
    assert [c["id"] for c in bff_dev_body["data"]["document_checked"]] == ["adm-5dc7be51a48996e4"]
    assert bff_dev_body["data"]["scaffolded"] == []

    bff_other = bff_client.get("/bff/alpha-factory", headers=_auth_headers("tenant-other"))
    assert bff_other.status_code == 200
    bff_other_body = bff_other.json()
    assert [c["strategy_spec_id"] for c in bff_other_body["items"]] == [other_spec_id]
    assert [c["id"] for c in bff_other_body["data"]["scaffolded"]] == ["adm-other-2303"]
    assert bff_other_body["data"]["replicated"] == []

    # 3. Cross-tenant header spoofing is rejected with 403 at BFF boundary
    spoof = bff_client.get(
        "/bff/alpha-factory",
        headers={
            "Authorization": _hs256_bearer("tenant-dev"),
            "X-Tenant-Id": "tenant-other",
        },
    )
    assert spoof.status_code == 403


def test_alpha_factory_requires_auth(monkeypatch) -> None:
    client = _make_bff_client(
        monkeypatch,
        http_get=lambda url, **kw: (200, []),
    )
    assert client.get("/bff/alpha-factory").status_code == 401
    assert (
        client.get(
            "/bff/alpha-factory",
            headers=_auth_headers("tenant-dev", roles=["guest"]),
        ).status_code
        == 403
    )


def test_alpha_factory_cards_removed_from_read_surface_ports() -> None:
    for cls in (
        WorkflowHookCatalogReaderPort,
        DomainWorkflowCatalogPort,
        CompositeOperationsConsultationPort,
        InMemoryOperationsConsultationPort,
        ReadSurfacePorts,
    ):
        assert not hasattr(cls, "list_alpha_factory_cards"), (
            f"{cls.__name__} must not retain dead list_alpha_factory_cards reader"
        )
    assert "alpha_factory_cards" not in ReadSurfacePorts._OPERATIONS_CONSULTATION_OWNED_DATASETS


# --------------------------------------------------------------------------- #
# BFF-EVO-ALPHA-PAGE-TRUTH-20261107: failure facts and strategy_state on cards
# --------------------------------------------------------------------------- #

_SPY = "strat-src-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e-spy-anchor-7e96a8404f3c"
_TW = "strat-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-60f9c8a78715"
_H12_A = "strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8"
_H12_B = "strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a"


def _hosted_four_entries() -> List[Dict[str, Any]]:
    """The four real hosted queue records as the owner route returns them."""
    rows = [
        (_SPY, 0, None, 0, 1),
        (_TW, 1, "2026-10-16T02:54:42Z", 1, 4),
        (_H12_A, 1, "2026-10-24T02:28:22Z", 1, 4),
        (_H12_B, 0, None, 0, 1),
    ]
    entries = []
    for idx, (strategy_id, replay_count, replayed_at, _unused, _runs) in enumerate(rows):
        entries.append(
            {
                "admission_id": f"adm-hosted-{idx}",
                "tenant_id": "tenant-dev",
                "strategy_spec_id": f"spec-{idx}",
                "strategy_id": strategy_id,
                "spec_version": "1.0.0",
                "checksum": VALID_CHECKSUM,
                "approval_decision_id": f"approval-{idx}",
                "approver": "pantheon-dev-approver",
                "approved_at": "2026-10-01T01:00:00Z",
                "admission_status": "admitted",
                "status": "admitted",
                "queue_status": "completed",
                "attempt_count": 0,
                "max_attempts": 3,
                "experiment_run_ids": ["run-1"],
                "authority_run_ids": ["run-a"],
                "last_revalidation_at": "2026-10-24T02:30:00Z",
                "last_error": None,
                "replay_count": replay_count,
                "last_replayed_at": replayed_at,
                "revalidation_count": 1,
            }
        )
    return entries


_HOSTED_STATES = {_SPY: "approved", _TW: "approved", _H12_A: "retired", _H12_B: "retired"}


def test_alpha_factory_cards_carry_failure_replay_facts_and_strategy_state(monkeypatch) -> None:
    reads: List[str] = []

    def reader(strategy_id: str) -> Tuple[Optional[str], str]:
        reads.append(strategy_id)
        return _HOSTED_STATES[strategy_id], "ok"

    entries = _hosted_four_entries()
    # Two cards share one strategy: the registry must still be read once for it.
    entries.append({**entries[2], "admission_id": "adm-hosted-dup", "strategy_spec_id": "spec-dup"})
    entries[1]["last_error"] = "experiment run timed out"
    entries[1]["attempt_count"] = 2

    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router(entries, gate_all_completed=True),
        read_strategy_state=reader,
    )
    payload = client.get("/bff/alpha-factory?page_size=100", headers=_auth_headers("tenant-dev")).json()

    assert sorted(reads) == sorted(_HOSTED_STATES)
    assert len(reads) == len(set(reads)) == 4
    assert payload["meta"]["surfaces"]["alpha_factory_strategy_state"]["status"] == "ok"

    cards = {c["admission_id"]: c for c in payload["data"]["items"]}
    assert len(cards) == 5
    assert cards["adm-hosted-1"]["last_error"] == "experiment run timed out"
    assert cards["adm-hosted-1"]["attempt_count"] == 2
    assert cards["adm-hosted-2"]["replay_count"] == 1
    assert cards["adm-hosted-2"]["last_replayed_at"] == "2026-10-24T02:28:22Z"
    assert cards["adm-hosted-2"]["revalidation_count"] == 1
    assert cards["adm-hosted-0"]["replay_count"] == 0
    assert cards["adm-hosted-0"]["last_replayed_at"] is None
    assert {cid: c["strategy_state"] for cid, c in cards.items()} == {
        "adm-hosted-0": "approved",
        "adm-hosted-1": "approved",
        "adm-hosted-2": "retired",
        "adm-hosted-3": "retired",
        "adm-hosted-dup": "retired",
    }

    # Retired strategies are not hidden or moved: lane rules are unchanged.
    lanes = {lane["id"]: lane for lane in payload["data"]["lanes"]}
    assert len(lanes["replicated"]["items"]) == 5
    assert lanes["scaffolded"]["items"] == []
    assert lanes["discovered"] == {"id": "discovered", "label": "Discovered", "items": []}


def test_alpha_factory_failed_strategy_state_read_keeps_cards_and_marks_surface(monkeypatch) -> None:
    def reader(strategy_id: str) -> Tuple[Optional[str], str]:
        if strategy_id == _H12_A:
            raise RuntimeError("registry down")
        if strategy_id == _TW:
            return None, "unavailable"
        return "approved", "ok"

    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router(_hosted_four_entries(), gate_all_completed=True),
        read_strategy_state=reader,
    )
    resp = client.get("/bff/alpha-factory", headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["meta"]["surfaces"]["alpha_factory_strategy_state"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["alpha_factory"]["status"] == "ok"
    assert payload["page_info"]["total"] == 4
    states = {c["strategy_id"]: c["strategy_state"] for c in payload["data"]["items"]}
    assert states == {_SPY: "approved", _TW: None, _H12_A: None, _H12_B: "approved"}
    assert len(payload["data"]["lanes"][2]["items"]) == 4


def test_strategies_service_state_reader_uses_registry_detail_and_degrades() -> None:
    from services.control_plane.bff.strategies.service import StrategiesService

    class _Store:
        def __init__(self) -> None:
            self.detail_calls: List[str] = []

        def get_strategy_spec_detail(self, strategy_id, version_selector="current"):
            self.detail_calls.append(strategy_id)
            if strategy_id == "boom":
                raise RuntimeError("registry down")
            if strategy_id == "missing":
                return None
            return {"strategy_id": strategy_id, "lifecycle_state": "retired"}

    store = _Store()
    svc = StrategiesService(
        read_surface=store,
        normalize_lifecycle_state=lambda s: str(s).strip().lower(),
    )
    assert svc.get_strategy_registry_state(_H12_A) == ("retired", "ok")
    assert store.detail_calls == [_H12_A]
    assert svc.get_strategy_registry_state("missing") == (None, "ok")
    assert svc.get_strategy_registry_state("boom") == (None, "unavailable")


# --------------------------------------------------------------------------- #
# BFF-ALPHA-FACTORY-PIPELINE-TRUTH-20261125
# --------------------------------------------------------------------------- #


def _demand(demand_id: str, persona_id: str, **extra: Any) -> Dict[str, Any]:
    return {
        "demand_id": demand_id,
        "tenant_id": "tenant-dev",
        "persona_id": persona_id,
        "status": "open",
        "short_measure": "return_per_risk",
        "missing_datasets": ["tw_price_daily"],
        "tried_families": ["momentum"],
        "created_at": "2026-11-20T01:00:00Z",
        **extra,
    }


def _search_run(run_id: str, demand_id: str, created_at: str, *, reason: Optional[str], status: str = "failed") -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "tenant_id": "tenant-dev",
        "status": status,
        "created_at": created_at,
        "parameters": {"action_type": "template_search", "demand_id": demand_id, "persona_id": "persona-a"},
        "rejection": {"detail": reason} if reason else {},
    }


def _get(client: TestClient, path: str = "/bff/alpha-factory?page_size=100") -> Dict[str, Any]:
    resp = client.get(path, headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    return resp.json()


def test_discovered_lane_lists_open_demands_and_hypothesis_cards(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.local")
    seeds = [
        {
            "seed_id": "seed-1",
            "source_id": "src-cna-1",
            "created_at": "2026-11-21T00:00:00Z",
            "status": "pending_review",
            "metadata": {
                "hypothesis_card": {"claim": "Earnings surprise drifts for 60 days", "cited_spans": []},
            },
        },
        {"seed_id": "seed-nocard", "source_id": "src-2", "metadata": {}},
    ]
    demands = [_demand("dem-1", "persona-a", discovery={"template_search": {"reason": "mandate_universe_missing: no tickers"}})]
    demands.append({**_demand("dem-closed", "persona-b"), "status": "closed"})
    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router([], demands=demands, seeds=seeds),
        read_strategy_state=None,
    )
    body = _get(client)
    discovered = body["data"]["discovered"]
    assert [d["id"] for d in discovered] == ["dem-1"]
    demand = discovered[0]
    assert demand["kind"] == "research_demand"
    assert demand["lacking"]["short_measure"] == "return_per_risk"
    assert demand["lacking"]["missing_datasets"] == ["tw_price_daily"]
    assert demand["since"] == "2026-11-20T01:00:00Z"
    assert demand["latest_outcome"]["reason_code"] == "mandate_universe_missing"
    # no bounded owner read for seeds yet: cards unavailable, never a scan, never 0
    assert body["data"]["counts"]["hypothesis_cards"] == {
        "value": None,
        "status": "unavailable",
        "reason": "no_bounded_owner_read",
    }
    assert body["data"]["counts"]["collected_text_records"]["reason"] == "no_bounded_owner_read"
    assert body["data"]["counts"]["dropped_irrelevant"]["reason"] == "no_bounded_owner_read"


def test_board_never_requests_unbounded_owner_routes(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.local")
    calls: List[str] = []
    client = _make_bff_client(
        monkeypatch, http_get=_owner_router([], demands=[_demand("dem-1", "persona-a")], calls=calls)
    )
    _get(client)
    from services.control_plane.bff.console_gap import alpha_factory as af

    for url in calls:
        assert not any(p in url for p in af.FORBIDDEN_UNBOUNDED_PATHS), url


def test_run_list_is_capped_and_marks_truncation(monkeypatch) -> None:
    runs = [
        {"run_id": f"r{i}", "tenant_id": "tenant-dev", "created_at": f"2026-11-{1 + i % 28:02d}T00:00:00Z"}
        for i in range(260)
    ]
    client = _make_bff_client(monkeypatch, http_get=_owner_router([], runs=runs))
    body = _get(client)
    surface = body["meta"]["surfaces"]["alpha_factory_runs"]
    assert surface["truncated"] is True and surface["runs_seen"] == 260


def test_persona_display_name_comes_from_the_persona_directory(monkeypatch) -> None:
    router = create_alpha_factory_router(
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        utc_now=lambda: FIXED_NOW,
        http_get=_owner_router([], demands=[_demand("dem-1", "persona-a")]),
        read_persona_names=lambda tenant, snap: {"persona-a": "Alpha Hunter"} if tenant == "tenant-dev" else {},
    )
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-orchestrator.local")
    app = FastAPI(middleware=[Middleware(OwnerReadContextMiddleware)])
    app.include_router(router)
    body = _get(TestClient(app))
    assert body["data"]["discovered"][0]["display_name"] == "Alpha Hunter"
    assert body["meta"]["surfaces"]["alpha_factory_persona_names"]["status"] == "ok"


def test_empty_discovered_lane_means_owners_answered_none(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.local")
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router([])))
    assert body["data"]["discovered"] == []
    surfaces = body["meta"]["surfaces"]
    assert surfaces["alpha_factory_demands"]["status"] == "ok"
    assert surfaces["alpha_factory_hypothesis_cards"]["reason"] == "no_bounded_owner_read"
    assert "no_candidate_source" not in json.dumps(body)


def test_failed_demand_read_is_unavailable_not_empty(monkeypatch) -> None:
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router([], fail=("persona-demands",))))
    assert body["meta"]["surfaces"]["alpha_factory_demands"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["alpha_factory_demands"]["reason"] == "owner_read_failed"


def test_demand_of_another_tenant_makes_the_demand_read_unavailable(monkeypatch) -> None:
    other = {**_demand("dem-x", "persona-x"), "tenant_id": "tenant-other"}
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router([], demands=[other])))
    assert body["data"]["discovered"] == []
    assert body["meta"]["surfaces"]["alpha_factory_demands"]["reason"] == "demand_scope_mismatch"


def test_scaffolded_lane_lists_template_searches_with_typed_reason(monkeypatch) -> None:
    runs = [
        _search_run("r1", "dem-1", "2026-11-22T00:00:00Z", reason="survivorship_limited"),
        _search_run("r2", "dem-1", "2026-11-23T00:00:00Z", reason="dataset tw_price_daily unavailable"),
        _search_run("r3", "dem-2", "2026-11-23T00:00:00Z", reason=None, status="completed"),
        _search_run("r4", "dem-unknown", "2026-11-23T00:00:00Z", reason="below_threshold"),
        {**_search_run("r5", "dem-1", "2026-11-24T00:00:00Z", reason="below_threshold"), "tenant_id": "tenant-other"},
    ]
    demands = [_demand("dem-1", "persona-a"), _demand("dem-2", "persona-b")]
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router([], runs=runs, demands=demands)))
    searches = {i["demand_id"]: i for i in body["data"]["scaffolded"] if i["kind"] == "template_search"}
    assert set(searches) == {"dem-1", "dem-2"}
    assert searches["dem-1"]["attempt_count"] == 2
    assert searches["dem-1"]["reason_code"] == "dataset_unavailable"
    assert searches["dem-1"]["latest_run_id"] == "r2"
    assert searches["dem-2"]["reason_code"] is None
    assert body["data"]["counts"]["searches"] == {"value": 5 - 1, "status": "ok"}


def test_gate_attempt_failure_reason_is_typed(monkeypatch) -> None:
    entries = _hosted_owner_entries()
    entries[2]["last_error"] = "below_threshold: oos sharpe 0.1"
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router(entries)))
    failed = next(c for c in body["data"]["scaffolded"] if c.get("id") == "adm-failed-2454")
    assert failed["reason_code"] == "below_threshold"
    assert failed["attempt_count"] == 2


def test_document_checked_admissions_never_reach_replicated(monkeypatch) -> None:
    entries = _hosted_four_entries()
    states = _HOSTED_STATES
    # run-1 has a failed gate; nothing passes. Entry 1 is a raw-market seed, entry 3 a simulation.
    entries[1]["datasets"] = ["raw_market"]
    entries[3]["mode"] = "simulation"
    client = _make_bff_client(
        monkeypatch,
        http_get=_owner_router(entries, runs=[_gate_run("run-1", passed=False)]),
        read_strategy_state=lambda sid: (states[sid], "ok"),
    )
    body = _get(client)
    assert body["data"]["replicated"] == []
    checked = {c["admission_id"]: c["document_check_reason"] for c in body["data"]["document_checked"]}
    assert checked == {
        "adm-hosted-0": "no_passing_gate_result",
        "adm-hosted-1": "simulation_or_raw_market_seed",
        "adm-hosted-2": "retired",
        "adm-hosted-3": "retired",
    }
    assert body["data"]["counts"]["passes"] == {"value": 0, "status": "ok"}
    assert not any(c["lane"] == "replicated" for c in body["items"])
    only = _get(client, "/bff/alpha-factory?lane=document_checked")
    assert only["page_info"]["total"] == 4


def test_passing_gate_run_recorded_by_owner_makes_a_replicated_card(monkeypatch) -> None:
    entries = _hosted_four_entries()
    client = _make_bff_client(monkeypatch, http_get=_owner_router(entries, runs=[_gate_run("run-1")]))
    body = _get(client)
    assert len(body["data"]["replicated"]) == 4
    assert body["data"]["document_checked"] == []
    assert body["data"]["counts"]["passes"] == {"value": 4, "status": "ok"}


def test_failed_run_read_never_promotes_to_replicated(monkeypatch) -> None:
    client = _make_bff_client(
        monkeypatch, http_get=_owner_router(_hosted_four_entries(), fail=("research-orchestrator/runs",))
    )
    body = _get(client)
    assert body["data"]["replicated"] == []
    assert {c["document_check_reason"] for c in body["data"]["document_checked"]} == {"gate_result_unavailable"}
    counts = body["data"]["counts"]
    assert counts["passes"]["status"] == "unavailable" and counts["passes"]["value"] is None
    assert counts["searches"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["alpha_factory_runs"]["reason"] == "runs_read_failed"


def test_counts_are_unavailable_with_reason_never_zero(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_URL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_SERVICE_URL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_URL", raising=False)
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router([], fail=("alpha-templates",))))
    counts = body["data"]["counts"]
    assert counts["collected_text_records"] == {"value": None, "status": "unavailable", "reason": "no_bounded_owner_read"}
    assert counts["hypothesis_cards"]["status"] == "unavailable"
    assert counts["templates_registered"] == {"value": None, "status": "unavailable", "reason": "templates_read_failed"}
    assert counts["dropped_irrelevant"]["status"] == "unavailable"
    assert counts["dropped_irrelevant"]["value"] is None


def test_counts_from_owner_reads(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.local")
    templates = [
        {"template_id": "t1", "evaluation": {"pooled_ic": {}}},
        {"template_id": "t2", "evaluation": None},
        {"template_id": "t3"},
    ]
    records = [{"source_id": f"s{i}"} for i in range(7)]
    body = _get(_make_bff_client(monkeypatch, http_get=_owner_router([], templates=templates, records=records)))
    counts = body["data"]["counts"]
    assert counts["collected_text_records"]["status"] == "unavailable"
    assert counts["templates_registered"] == {"value": 3, "status": "ok"}
    assert counts["templates_evaluated"] == {"value": 1, "status": "ok"}
    assert counts["searches"] == {"value": 0, "status": "ok"}


def test_alpha_factory_route_is_plain_def_and_reads_only(monkeypatch) -> None:
    import inspect

    methods: List[str] = []
    base = _owner_router([])

    def recording(url: str, **kw: Any):
        methods.append("GET")
        return base(url, **kw)

    client = _make_bff_client(monkeypatch, http_get=recording)
    router = create_alpha_factory_router(
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        utc_now=lambda: FIXED_NOW,
    )
    route = next(r for r in router.routes if getattr(r, "path", "") == "/bff/alpha-factory")
    assert not inspect.iscoroutinefunction(route.endpoint)
    assert route.methods == {"GET"}
    _get(client)
    assert set(methods) == {"GET"}
    assert len(methods) <= 6


def test_reason_code_normalisation() -> None:
    from services.control_plane.bff.console_gap.alpha_factory import reason_code

    assert reason_code(None) == "reason_not_recorded"
    assert reason_code("Mandate_Universe_Missing: x") == "mandate_universe_missing"
    assert reason_code("dataset missing for X") == "dataset_unavailable"
    assert reason_code("whole_boundary_control_failed:ValueError:boom") == "whole_boundary_control_failed"
