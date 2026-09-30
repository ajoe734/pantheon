"""Every approval entry point is scoped to the caller tenant (real JWTs, mounted router)."""
from __future__ import annotations

import time
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_jwt,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.governance.router import create_governance_router
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET, ISS, AUD = "tenant-entry-secret", "tenant-entry-iss", "tenant-entry-aud"
MEMO = "tenant-a private memo"


class _Store:
    def __init__(self) -> None:
        self.record = {
            "id": "owner-a",
            "decision_id": "owner-a",
            "tenant_id": "tenant-a",
            "decision_state": "pending",
            "decision_type": "DeploymentPlan",
            "memo": MEMO,
            "evidence_refs": [{"ref_id": "ev-a"}],
        }

    source = "local_snapshot"

    def dataset_source(self, _dataset: str) -> str:
        return self.source

    def list_approval_decisions(self, **_: Any) -> List[Dict[str, Any]]:
        return [dict(self.record)]

    def get_approval_decision(self, decision_id: str) -> Any:
        return dict(self.record) if decision_id == "owner-a" else None

    def list_approval_queue_items(self, **_: Any) -> List[Dict[str, Any]]:
        return [dict(self.record)]


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", ISS)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", AUD)
    submitted: List[Dict[str, Any]] = []

    async def submit_action(**kwargs: Any) -> Dict[str, Any]:
        submitted.append(kwargs)
        return {"data": {"action": kwargs["action_id"], "command_id": "cmd-1", "commandId": "cmd-1"}}

    store = _Store()
    app = FastAPI()
    app.include_router(
        create_governance_router(
            get_read_store=lambda: store,
            extract_identity=extract_identity_jwt,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            submit_action=submit_action,
        )
    )
    return TestClient(app), submitted


def _headers(tenant: str, key: str = "") -> Dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": f"op-{tenant}",
            "tenant_id": tenant,
            "roles": ["approver", "operator", "viewer"],
            "iss": ISS,
            "aud": AUD,
            "exp": time.time() + 3600,
        },
        secret=SECRET,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def test_same_tenant_caller_reaches_every_entry_point(env: Any) -> None:
    client, submitted = env
    a = _headers("tenant-a", "k-a")
    assert client.get("/api/v1/approval-decisions", headers=a).json()["data"][0]["memo"] == MEMO
    assert client.get("/api/v1/approval-decisions/owner-a", headers=a).status_code == 200
    assert [i["decision_id"] for i in client.get("/bff/approvals", headers=a).json()["items"]] == ["owner-a"]
    assert client.get("/bff/approvals/owner-a", headers=a).json()["data"]["memo"] == MEMO
    assert client.get("/bff/approvals/owner-a/evidence", headers=a).status_code == 200
    assert client.post("/bff/approvals/owner-a/decide", json={"decision": "approve"}, headers=a).status_code == 202
    batch = client.post("/bff/approvals/batch-decide", json={"decisions": [{"id": "owner-a", "decision": "approve"}]}, headers=_headers("tenant-a", "k-b"))
    assert batch.status_code == 202
    created = client.post("/api/v1/approval-decisions", json={"plan_id": "p1", "decision": "approve", "memo": "Approved with evidence"}, headers=_headers("tenant-a", "k-c"))
    assert created.status_code == 202
    assert len(submitted) == 2


def test_cross_tenant_caller_sees_and_decides_nothing(env: Any) -> None:
    client, submitted = env
    b = _headers("tenant-b", "k-x")
    assert client.get("/api/v1/approval-decisions", headers=b).json()["data"] == []
    assert client.get("/api/v1/approval-decisions/owner-a", headers=b).status_code == 404
    assert client.get("/bff/approvals", headers=b).json()["items"] == []
    assert client.get("/bff/approvals/owner-a", headers=b).status_code == 404
    assert client.get("/bff/approvals/owner-a/evidence", headers=b).status_code == 404
    assert client.post("/bff/approvals/owner-a/decide", json={"decision": "approve"}, headers=b).status_code == 404
    batch = client.post("/bff/approvals/batch-decide", json={"decisions": [{"id": "owner-a", "decision": "approve"}]}, headers=_headers("tenant-b", "k-y"))
    assert batch.json()["results"][0]["status"] == "failed"
    assert submitted == []
    assert MEMO not in batch.text


def test_create_idempotency_cache_is_tenant_scoped(env: Any) -> None:
    client, _ = env
    body = {"plan_id": "p1", "decision": "approve", "memo": "Approved with evidence"}
    first = client.post("/api/v1/approval-decisions", json=body, headers=_headers("tenant-a", "same-key")).json()["data"]
    second = client.post("/api/v1/approval-decisions", json=body, headers=_headers("tenant-b", "same-key")).json()["data"]
    assert second["commandId"] != first["commandId"]
    assert second["approver_id"] == "op-tenant-b"
    replay = client.post("/api/v1/approval-decisions", json=body, headers=_headers("tenant-a", "same-key")).json()["data"]
    assert replay["commandId"] == first["commandId"]


def test_missing_dataset_source_does_not_bypass_tenant_denial(env: Any) -> None:
    client, submitted = env
    _Store.source = "missing"
    try:
        b = _headers("tenant-b", "k-m")
        assert client.post("/bff/approvals/owner-a/decide", json={"decision": "approve"}, headers=b).status_code == 404
        batch = client.post("/bff/approvals/batch-decide", json={"decisions": [{"id": "owner-a", "decision": "approve"}]}, headers=_headers("tenant-b", "k-n"))
        assert batch.json()["results"][0]["status"] == "failed"
        assert submitted == []
    finally:
        _Store.source = "local_snapshot"


def test_create_idempotency_key_is_not_ambiguous_across_tenants(env: Any) -> None:
    client, _ = env
    body = {"plan_id": "p1", "decision": "approve", "memo": "Approved with evidence"}
    first = client.post("/api/v1/approval-decisions", json=body, headers=_headers("tenant-a", "b::key")).json()["data"]
    second = client.post("/api/v1/approval-decisions", json=body, headers=_headers("tenant-a::b", "key")).json()["data"]
    assert second["commandId"] != first["commandId"]
    assert second["approver_id"] == "op-tenant-a::b"


@pytest.mark.parametrize("alias", ["decision_id", "id"])
def test_caller_supplied_id_cannot_shadow_another_tenant_approval(env: Any, alias: str) -> None:
    client, submitted = env
    b = _headers("tenant-b", "k-s")
    body = {"plan_id": "p1", "decision": "approve", "memo": "Approved with evidence", alias: "owner-a"}
    assert client.post("/api/v1/approval-decisions", json=body, headers=b).status_code == 202
    assert client.post("/bff/approvals/owner-a/decide", json={"decision": "approve"}, headers=b).status_code == 404
    batch = client.post("/bff/approvals/batch-decide", json={"decisions": [{"id": "owner-a", "decision": "approve"}]}, headers=_headers("tenant-b", "k-t"))
    assert batch.json()["results"][0]["status"] == "failed"
    assert submitted == []
