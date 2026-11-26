"""MGMT-VIEWER-APPROVALS-READ-20261126: who may read pending approvals.

Rule (BFF_API_CONTRACT.md section 8 and the cited-evidence note; Governance
``services/governance/main.py::_approval_reader``): a ``viewer`` is read-only
on non-sensitive surfaces and may not read approvals; ``operator`` and the
approver principal (``approver`` + ``governance_reviewer``) may. The BFF does
not widen the owner rule: it answers the owner's refusal on every approval
read with a typed ``FORBIDDEN`` (``precondition_failed=approval_read_role``)
so the page can render a no-permission state, and leaves write paths as they
were.

Everything below is real except persistence (JSON store in ``tmp_path``) and
the socket: dev-login issues the JWTs, the Governance app verifies them and
applies its role rule, and the BFF router runs with the production auth,
error and redaction dependencies. ``urllib.request.urlopen`` is routed to the
Governance ``TestClient`` instead of a TCP connection.
"""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff import models
from services.control_plane.bff.auth import policy
from services.control_plane.bff.auth.handlers import create_auth_handlers
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore

GOV_URL = "http://governance.test"
TENANT = "tenant-dev"
PENDING_ID = "apv-viewer-read-pending"
DECIDED_ID = "apv-viewer-read-decided"
# One ref the approver's capabilities admit (approval.read) and one that no
# non-admin role here holds, so redaction differs per principal.
EVIDENCE = [
    {"ref_type": "approval-decision", "ref_id": "apv-upstream-1", "note": "upstream approval"},
    {"ref_type": "audit_log_entry", "ref_id": "audit-entry-1", "note": "audit trail"},
]


def _decision(decision_id: str, state: str) -> Dict[str, Any]:
    decided = state == "decided"
    return {
        "decision_id": decision_id, "target_type": "registry_entry", "target_id": f"reg-{decision_id}",
        "target_version": "1.0.0", "decision": "approved" if decided else None, "decision_state": state,
        "actor_role": "governance_reviewer" if decided else None,
        "actor_id": "pantheon-dev-approver" if decided else None,
        "rationale": "recorded" if decided else None, "created_at": "2026-11-26T00:00:00Z",
        "decided_at": "2026-11-26T00:05:00Z" if decided else None, "conditions": [], "risk_level": "low",
        "evidence_refs": [dict(ref) for ref in EVIDENCE], "tenant_id": TENANT,
        "owner_user_id": "pantheon-dev-operator", "version": 2 if decided else 1,
        "event_id": f"evt-{decision_id}-{2 if decided else 1}",
    }


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def stack(tmp_path, monkeypatch):
    for name, value in {
        "PANTHEON_ENV": "dev", "PANTHEON_DEPLOYMENT_STAGE": "dev", "PANTHEON_PERSISTENCE_POSTURE": "dev",
        "PANTHEON_BFF_AUTH_MODE": "strict", "PANTHEON_BFF_AUTH_STUB": "false",
        "PANTHEON_BFF_JWT_SECRET": "synthetic-viewer-approvals-read-secret",
        "PANTHEON_BFF_DEV_LOGIN_JWT_SECRET": "synthetic-viewer-approvals-read-secret",
        "PANTHEON_BFF_JWT_ISSUER": "synthetic-dev-login", "PANTHEON_BFF_JWT_AUDIENCE": "synthetic-dev-owners",
        "GOVERNANCE_DATA_DIR": str(tmp_path / "governance"),
        "GOVERNANCE_STORE_BACKEND": "json", "GOVERNANCE_AUDIT_BACKEND": "jsonl",
        "PANTHEON_GOVERNANCE_APPROVAL_API_URL": GOV_URL,
    }.items():
        monkeypatch.setenv(name, value)
    for suffix in ("JWKS_URI", "OIDC_DISCOVERY_URL", "OIDC_ISSUER", "OIDC_AUDIENCE",
                   "ROLE_MAP", "ROLE_MAP_MODE", "ROLE_CLAIMS"):
        for owner in ("BFF", "GOVERNANCE"):
            monkeypatch.delenv(f"PANTHEON_{owner}_{suffix}", raising=False)
    for suffix in ("SECRET", "ISSUER", "AUDIENCE"):
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_JWT_{suffix}", os.environ[f"PANTHEON_BFF_JWT_{suffix}"])
    for identity in ("viewer", "operator", "approver"):
        prefix = "PANTHEON_BFF_DEV_LOGIN_" + identity.upper()
        monkeypatch.setenv(prefix + "_CLIENT_ID", "synthetic-" + identity)
        monkeypatch.setenv(prefix + "_CLIENT_SECRET", "password-" + identity)
        monkeypatch.setenv(prefix + "_TENANT_ID", TENANT)
        monkeypatch.setenv(prefix + "_ALLOWED_TENANTS", TENANT)

    from services.governance import main as governance
    from services.governance.pg_store import ApprovalDecisionStore
    from services.control_plane.governance.approval_decision import ApprovalDecision

    store = ApprovalDecisionStore(str(tmp_path / "decisions.json"))
    for record in (_decision(PENDING_ID, "proposed"), _decision(DECIDED_ID, "decided")):
        store.put(ApprovalDecision.from_dict(record))
    monkeypatch.setattr(governance, "store", store)
    governance_client = TestClient(governance.app)

    calls = []
    real_urlopen = urllib.request.urlopen

    def owner_urlopen(req, timeout=None, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if not url.startswith(GOV_URL):
            return real_urlopen(req, timeout=timeout, *a, **kw)
        method = req.get_method()
        calls.append((method, url[len(GOV_URL):]))
        response = governance_client.request(
            method, url[len(GOV_URL):], content=req.data,
            headers={k: v for k, v in req.header_items()},
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, response.reason_phrase, {},
                                         io.BytesIO(response.content))
        return _Resp(response.content)

    monkeypatch.setattr(urllib.request, "urlopen", owner_urlopen)

    deps = policy.create_auth_dependencies(
        session_lifecycle_store=SessionLifecycleStore(str(tmp_path / "sessions.json")))
    handlers = create_auth_handlers(dependencies=deps)
    bff = FastAPI()
    register_error_handlers(bff)
    bff.include_router(create_auth_router(
        service=AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)))
    bff.include_router(create_governance_router(
        extract_identity=policy.extract_identity,
        require_read_role=policy.require_read_role,
        require_operator_role=policy.require_operator_role,
        bff_error=policy.bff_error,
        redact_evidence_refs=models.redact_evidence_refs,
        capabilities_for_identity=policy.capabilities_for_identity,
    ))
    client = TestClient(bff)

    def login(identity: str) -> Dict[str, str]:
        response = client.post("/bff/auth/dev-login", json={
            "grant_type": "client_credentials", "client_id": "synthetic-" + identity,
            "client_secret": "password-" + identity})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    return {"client": client, "login": login, "calls": calls, "store": store, "governance": governance_client}


def _assert_typed_no_permission(response) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["i18nKey"] == "errors.FORBIDDEN"
    assert error["retryable"] is False
    details = error["details"]
    assert details["precondition_failed"] == "approval_read_role"
    assert details["permission_state"] == "no_permission"
    assert details["surface"] == "approvals"
    assert details["owner"] == "governance"
    # Nothing from the approval records leaks into the refusal.
    assert PENDING_ID not in response.text and "apv-upstream-1" not in response.text


@pytest.mark.parametrize("path", [
    "/bff/approvals",
    f"/bff/approvals/{PENDING_ID}",
    f"/bff/approvals/{PENDING_ID}/evidence",
    "/bff/management/governance-ledger",
])
def test_viewer_approval_reads_answer_typed_no_permission(stack, path):
    headers = stack["login"]("viewer")
    response = stack["client"].get(path, headers=headers)
    _assert_typed_no_permission(response)
    # The owner made the decision (one GET with the viewer's own token); the BFF did not pre-empt it.
    assert [method for method, _ in stack["calls"]] == ["GET"]


def test_owner_itself_refuses_the_viewer_token(stack):
    headers = stack["login"]("viewer")
    denied = stack["governance"].get("/api/governance/approvals", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Approval read role required"


@pytest.mark.parametrize("identity", ["operator", "approver"])
def test_reader_roles_list_and_detail_with_redaction(stack, identity):
    headers = stack["login"](identity)
    listed = stack["client"].get("/bff/approvals", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["decision_id"] for item in body["items"]] == [PENDING_ID]  # pending only
    detail = stack["client"].get(f"/bff/approvals/{PENDING_ID}", headers=headers)
    assert detail.status_code == 200, detail.text

    identity_obj = policy.extract_identity(headers["Authorization"])
    expected, expected_count = models.redact_evidence_refs(
        identity_obj, [dict(ref) for ref in EVIDENCE],
        capabilities=policy.capabilities_for_identity(identity_obj))
    assert expected_count >= 1  # each reader role here lacks at least one capability
    for refs, count in (
        (body["items"][0]["evidence_refs"], body["meta"]["redacted_evidence_count"]),
        (detail.json()["data"]["evidence_refs"], detail.json()["meta"]["redacted_evidence_count"]),
    ):
        assert count == expected_count
        redacted = [ref for ref in refs if isinstance(ref, dict) and ref.get("redacted") is True]
        assert len(redacted) == expected_count
        for ref in redacted:  # a withheld ref keeps no note or storage pointer
            assert "note" not in ref and "storage_ref" not in ref
        assert [ref.get("ref_id") for ref in refs] == [e.get("ref_id") for e in expected]


def test_approver_sees_more_evidence_than_operator(stack):
    by_role = {}
    for identity in ("operator", "approver"):
        response = stack["client"].get(f"/bff/approvals/{PENDING_ID}", headers=stack["login"](identity))
        by_role[identity] = {ref["ref_id"] for ref in response.json()["data"]["evidence_refs"]
                             if not ref.get("redacted")}
    assert "apv-upstream-1" in by_role["approver"]
    assert "apv-upstream-1" not in by_role["operator"]


def test_viewer_write_paths_still_refused_and_untyped(stack):
    headers = stack["login"]("viewer")
    before = stack["store"].get(PENDING_ID).to_dict()
    decide = stack["client"].post(
        f"/bff/approvals/{PENDING_ID}/decide",
        headers={**headers, "Idempotency-Key": "viewer-decide-1"},
        json={"outcome": "approved", "rationale": "viewer attempt", "expected_version": 1,
              "actor_role": "governance_reviewer"},
    )
    assert decide.status_code == 403, decide.text
    # The write path keeps its existing owner pass-through: no approval_read_role typing.
    assert "approval_read_role" not in decide.text
    batch = stack["client"].post(
        "/bff/approvals/batch-decide",
        headers={**headers, "Idempotency-Key": "viewer-batch-1"},
        json={"decisions": [{"id": PENDING_ID, "outcome": "approved", "rationale": "viewer attempt",
                             "expected_version": 1, "actor_role": "governance_reviewer"}]},
    )
    assert batch.status_code == 207, batch.text
    assert batch.json()["summary"] == {"total": 1, "accepted": 0, "failed": 1}
    assert batch.json()["results"][0]["http_status"] == 403
    for path in (f"/api/governance/approvals/{PENDING_ID}/review",
                 f"/api/governance/approvals/{DECIDED_ID}/revoke"):
        owner = stack["governance"].post(path, headers={**headers, "Idempotency-Key": "viewer-" + path[-6:]},
                                         json={"expected_version": 1, "actor_role": "risk_owner",
                                               "actor_id": "pantheon-dev-viewer", "reason": "viewer attempt"})
        assert owner.status_code in (401, 403, 422), owner.text
        assert owner.status_code != 200
    assert stack["store"].get(PENDING_ID).to_dict() == before
    assert all(method == "POST" for method, path in stack["calls"] if path.endswith("/decide"))
