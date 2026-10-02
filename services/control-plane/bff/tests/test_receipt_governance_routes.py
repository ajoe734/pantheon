"""Legacy Governance writes and reads share the canonical approval owner."""
import base64
import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.test_bff_approvals_decide_contract import owner, jwt
from services.control_plane.bff.auth.policy import bff_error, require_operator_role, require_read_role
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_read_surface_ports


def test_governance_admission_owner_and_legacy_reads(owner, tmp_path):
    def identity(auth=None, **kwargs):
        if not auth:
            raise HTTPException(401)
        claims = json.loads(base64.urlsafe_b64decode(auth.split(".")[1] + "=="))
        return OperatorIdentity(operator_id=claims["sub"], roles=claims["roles"], claims=claims)

    ports = create_read_surface_ports()
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    service = CommandAdapterService(command_store=store, read_surface=ports, extract_identity=identity)
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(create_governance_router(
        read_surface=ports, extract_identity=identity, require_operator_role=require_operator_role,
        require_read_role=require_read_role, bff_error=bff_error,
        submit_action=service.submit_governance_action,
    ))
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + jwt("reviewer", "tenant-a", "operator", "approver", "governance_reviewer"),
               "Idempotency-Key": "decision"}
    path = "/bff/reviews/a1/actions/approve"
    payload = {"memo": "reviewed", "expected_version": 1, "actor_role": "governance_reviewer"}
    assert client.post(path, json=payload).status_code == 401
    result = client.post(path, json=payload, headers=headers)
    assert result.status_code == 202, result.text
    record = store.get_command_by_idempotency_key("decision", operator_id="reviewer")
    assert record["status"] == "executed", record
    assert owner.rows["a1"]["version"] == 2
    assert client.post(path, json=payload, headers=headers).status_code == 202
    assert len([call for call in owner.calls if call[0] == "POST"]) == 1
    queue = client.get("/api/v1/operator/governance/approval-queue", headers=headers)
    assert queue.status_code == 200, queue.text
    assert queue.json()["items"][0]["version"] == 2
    ledger = client.get("/bff/management/governance-ledger", headers=headers)
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["data"]["items"][0]["target_id"] == "a1"
    other = {**headers, "Authorization": "Bearer " + jwt("other", "tenant-b", "operator")}
    assert client.get("/api/v1/operator/governance/approval-queue", headers=other).json()["items"][0]["decision_id"] == "b1"
    assert client.post("/bff/reviews", json={}, headers=headers).status_code == 410
