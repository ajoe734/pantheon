"""Legacy Governance writes and reads share the canonical approval owner."""
import base64
import json
import asyncio
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

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


@pytest.mark.parametrize("owner_available", [True, False])
def test_sponsor_processor_uses_persisted_owner_not_local_projection(tmp_path, monkeypatch, owner_available):
    from services.consultation.models import ConsultRequest, ConsultMemo
    from services.consultation.store import ConsultationStore
    from services.control_plane.internal import internal_api
    from services.control_plane.bff.command_adapters import governance_adapter
    from services.control_plane.bff.command_adapters.service import process_command, set_command_auth_context
    from services.control_plane.bff.models import CommandType, ObjectType, TargetObject

    owner_dir = str(tmp_path / "consultation")
    owner_store = ConsultationStore(owner_dir)
    owner_store.put_request(ConsultRequest(
        request_id="request-a", request_type="execution_risk",
        requested_by={"actor_type": "operator", "actor_id": "reviewer"},
        target_type="deployment_plan", target_id="plan-a", trace_id="trace-a",
        metadata={"consultation": {"committee_ref": "committee-a"}},
    ))
    owner_store.put_memo(ConsultMemo(
        memo_id="memo-a", request_id="request-a", memo_type="redteam_report",
        author_type="persona", author_ref="reviewer", target_type="deployment_plan",
        target_id="plan-a", summary="Reviewed", recommendation="approve",
        status="published", trace_id="trace-a",
    ))
    monkeypatch.delenv("PANTHEON_CONSULTATION_API_URL", raising=False)
    monkeypatch.setenv("PANTHEON_RUNTIME_CONSULTATION_DATA_DIR", owner_dir)
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://isolated-owner")
    monkeypatch.setattr(internal_api, "_COMMAND_STATE_FILE", str(tmp_path / "internal-commands.json"))
    monkeypatch.setattr(internal_api, "validate_request_auth", lambda **kwargs:
                        SimpleNamespace(actor_id="reviewer", mfa_verified=True, mfa_token="123456"))
    calls = []

    def transport(url, *, method, payload, auth_token, mfa_token):
        assert auth_token == "reviewer:approver"
        assert mfa_token == "123456"
        calls.append(urlsplit(url).path)
        if not owner_available:
            raise HTTPException(503, detail="owner unavailable")
        response = internal_api.app.test_client().open(urlsplit(url).path, method=method, json=payload,
            headers={"Authorization": f"Bearer {auth_token}", "X-MFA-Token": mfa_token})
        assert response.status_code == 202, response.get_json()
        return response.get_json()

    def local_write(*args, **kwargs):
        pytest.fail("Sponsor commands must not write a BFF projection")

    monkeypatch.setattr(governance_adapter, "http_request_json", transport)
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    store.submit_command(command_id="sponsor", command_type=CommandType.RECORD_SPONSOR_DECISION,
        target=TargetObject(type=ObjectType.COMMITTEE_BOARD, id="committee-a"),
        submitted_at="2026-10-02T00:00:00Z", audit_context={"operator_id": "reviewer"},
        params={"committee_id": "committee-a", "sponsor_decision": "approved", "rationale_ref": "evidence://review-a"})
    set_command_auth_context("sponsor", {"auth_token": "reviewer:approver", "mfa_token": "123456"})
    asyncio.run(process_command("sponsor", command_store=store,
                               read_store=SimpleNamespace(record_sponsor_decision=local_write)))
    record = store.get_command("sponsor")
    assert calls == ["/api/internal/v1/consultations/committees/committee-a/sponsor-decision"]
    persisted = ConsultationStore(owner_dir)
    consultation = persisted.get_request("request-a").metadata["consultation"]
    handoffs = persisted.list_handoffs_for_request("request-a")
    if owner_available:
        assert record["status"] == "executed", record
        assert record["audit"]["downstream_verified"] is True
        assert consultation["sponsor_decision"] == "approved"
        assert consultation["synthesis_summary"]["rationale_ref"] == "evidence://review-a"
        assert len(handoffs) == 1
        assert record["result"]["authoritative_readback"]["service_handoff"]["handoff_id"] == handoffs[0].handoff_id
    else:
        assert record["status"] == "failed", record
        assert not record["audit"].get("downstream_verified")
        assert "sponsor_decision" not in consultation
        assert not handoffs
