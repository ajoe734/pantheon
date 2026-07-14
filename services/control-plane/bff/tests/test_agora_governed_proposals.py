from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import main as bff_main
from services.runtime_auth_inbound import encode_jwt_hs256

HEADERS = {"Authorization": "Bearer proposal-user:operator", "Idempotency-Key": "pint-004-create"}
JWT_SECRET = "pint-010-r2-approval-secret"
JWT_ISSUER = "pint-010-r2"
JWT_AUDIENCE = "pantheon-bff"


def payload():
    return {
        "proposal_type": "strategy_patch", "target_kind": "strategy", "target_id": "s-1",
        "target_version": "v1", "current_value": {"risk": .1}, "proposed_value": {"risk": .08},
        "rationale": "reduce drawdown", "evidence_refs": ["ev-1"], "confidence": .8,
        "expected_benefit": "lower drawdown", "adverse_scenarios": ["missed upside"],
        "environment_ceiling": "paper", "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "validation_plan": {"backtest": "bt-1"}, "rollback_trigger": "drawdown worsens",
        "rollback_action": "restore v1", "required_permissions": ["strategy.review"],
        "required_reviewers": ["risk"], "human_gate": True,
    }


def client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app, raise_server_exceptions=False)


def strict_client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    return TestClient(bff_main.app, raise_server_exceptions=False)


def jwt_authorization(subject, roles, *, user_id="proposal-owner"):
    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": subject,
            "user_id": user_id,
            "roles": roles,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + 3600,
        },
        secret=JWT_SECRET,
    )
    return f"Bearer {token}"


def validate_proposal(c, proposal_id, etag, *, authorization=HEADERS["Authorization"]):
    return c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": etag},
        json={
            "action": "validate",
            "reason": "paper checks passed",
            "validation_result": {"status": "passed"},
        },
    )


def authoritative_approval(*, approval_id="approval-risk-1", reviewer="risk-reviewer", **overrides):
    return {
        "id": approval_id,
        "decision_id": approval_id,
        "state": "decided",
        "outcome": "approved",
        "target_type": "strategy_spec",
        "target_id": "s-1",
        "target_version": "v1",
        "tenant_id": "pantheon-dev",
        "owner_user_id": "proposal-owner",
        "reviewer": reviewer,
        "actor_role": "risk_owner",
        "decided_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        **overrides,
    }


def test_revision_history_etag_and_governed_link(monkeypatch):
    c = client(monkeypatch)
    created = c.post("/bff/agora/proposals", headers=HEADERS, json=payload())
    assert created.status_code == 201, created.text
    pid, etag = created.json()["data"]["proposal_id"], created.headers["etag"]
    modified = c.post(f"/bff/agora/proposals/{pid}/actions", headers={"Authorization": HEADERS["Authorization"], "If-Match": etag}, json={"action": "modify", "reason": "review feedback", "proposed_value": {"risk": .07}})
    assert modified.status_code == 200
    assert modified.json()["data"]["target_version"] == "v1"
    validated = c.post(f"/bff/agora/proposals/{pid}/actions", headers={"Authorization": HEADERS["Authorization"], "If-Match": modified.headers["etag"]}, json={"action": "validate", "reason": "checks passed", "validation_result": {"status": "passed"}})
    assert validated.status_code == 200
    assert validated.json()["data"]["governed_action_link"]["execution_authority"] == "none"
    history = c.get(f"/bff/agora/proposals/{pid}/revisions", headers={"Authorization": HEADERS["Authorization"]})
    assert [r["revision"] for r in history.json()["data"]] == [1, 2, 3]


def test_conflicts_and_governance_ceiling_fail_closed(monkeypatch):
    c = client(monkeypatch)
    bad = payload(); bad["human_gate"] = False
    assert c.post("/bff/agora/proposals", headers={**HEADERS, "Idempotency-Key": "bad"}, json=bad).status_code == 422
    created = c.post("/bff/agora/proposals", headers={**HEADERS, "Idempotency-Key": "conflict"}, json=payload())
    pid = created.json()["data"]["proposal_id"]
    action = {"action": "modify", "reason": "change", "proposed_value": {"risk": .05}}
    assert c.post(f"/bff/agora/proposals/{pid}/actions", headers={"Authorization": HEADERS["Authorization"], "If-Match": '"stale"'}, json=action).status_code == 412
    assert c.post(f"/bff/agora/proposals/{pid}/actions", headers={"Authorization": HEADERS["Authorization"], "If-Match": created.headers["etag"]}, json={"action": "approve", "reason": "premature"}).status_code == 422


def test_create_rejects_timezone_naive_expiry_without_500(monkeypatch):
    c = client(monkeypatch)
    naive = payload()
    naive["expires_at"] = (datetime.now() + timedelta(days=1)).isoformat()

    response = c.post(
        "/bff/agora/proposals",
        headers={**HEADERS, "Idempotency-Key": "naive-expiry"},
        json=naive,
    )

    assert response.status_code == 422, response.text
    assert "timezone offset" in response.text


def test_create_idempotency_replays_and_payload_mismatch_conflicts(monkeypatch):
    c = client(monkeypatch)
    headers = {**HEADERS, "Idempotency-Key": "replay-key"}
    original = payload()
    first = c.post("/bff/agora/proposals", headers=headers, json=original)
    replay = c.post("/bff/agora/proposals", headers=headers, json=original)
    assert replay.status_code == 201
    assert replay.json()["data"]["proposal_id"] == first.json()["data"]["proposal_id"]
    changed = payload(); changed["proposed_value"] = {"risk": .01}
    assert c.post("/bff/agora/proposals", headers=headers, json=changed).status_code == 409


def test_proposal_exposes_only_authoritative_available_approval_refs(monkeypatch):
    c = client(monkeypatch)
    # Stub auth scopes the private proposal to the actor id rather than the
    # strict JWT user_id used by the approval action tests below.
    monkeypatch.setattr(
        bff_main.read_store,
        "list_approval_decisions",
        lambda: [
            authoritative_approval(
                approval_id="approval-risk-valid",
                owner_user_id="proposal-user",
            ),
            authoritative_approval(
                approval_id="approval-self",
                reviewer="proposal-user",
                owner_user_id="proposal-user",
            ),
            authoritative_approval(
                approval_id="approval-wrong-target",
                target_id="s-other",
                owner_user_id="proposal-user",
            ),
            authoritative_approval(
                approval_id="approval-pending",
                state="pending",
                owner_user_id="proposal-user",
            ),
            authoritative_approval(
                approval_id="approval-other-tenant",
                tenant_id="tenant-other",
                owner_user_id="proposal-user",
            ),
        ],
    )
    proposal_payload = payload()
    proposal_payload["required_reviewers"] = ["risk", "governance_committee"]
    created = c.post(
        "/bff/agora/proposals",
        headers={**HEADERS, "Idempotency-Key": "authoritative-refs"},
        json=proposal_payload,
    )

    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["available_approval_decision_refs"] == []
    assert data["approval_decision_refs_authority"] == "canonical_read_store"
    assert data["approval_decision_readiness"] == {
        "ready": False,
        "reason": "proposal_not_validated",
        "missing_required_reviewers": ["risk", "governance_committee"],
    }
    assert data["execution_authority"] == "none"

    injected = c.post(
        f"/bff/agora/proposals/{data['proposal_id']}/actions",
        headers={
            "Authorization": HEADERS["Authorization"],
            "If-Match": created.headers["etag"],
        },
        json={
            "action": "validate",
            "reason": "attempt to attach unverified ref",
            "validation_result": {"status": "passed"},
            "approval_refs": ["payload-controlled"],
        },
    )
    assert injected.status_code == 422
    assert "only accepted for approve" in injected.text


def test_approval_rejects_non_authoritative_ref_and_operator_role(monkeypatch):
    c = strict_client(monkeypatch)
    proposer_auth = jwt_authorization("proposal-user", ["operator"])
    operator_auth = jwt_authorization("other-operator", ["operator"])
    reviewer_auth = jwt_authorization("risk-reviewer", ["reviewer"])
    monkeypatch.setattr(bff_main.read_store, "get_approval_decision", lambda _ref: None)
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": proposer_auth, "Idempotency-Key": "approval-authority-negative"},
        json=payload(),
    )
    pid = created.json()["data"]["proposal_id"]
    validated = validate_proposal(c, pid, created.headers["etag"], authorization=proposer_auth)

    operator_only = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={"Authorization": operator_auth, "If-Match": validated.headers["etag"]},
        json={"action": "approve", "reason": "not authorized", "approval_refs": ["made-up"]},
    )
    assert operator_only.status_code == 403

    unverified = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={"Authorization": reviewer_auth, "If-Match": validated.headers["etag"]},
        json={"action": "approve", "reason": "unverified ref", "approval_refs": ["made-up"]},
    )
    assert unverified.status_code == 422
    assert "not authoritative" in unverified.text


def test_approval_rejects_self_approval_and_target_mismatch(monkeypatch):
    c = strict_client(monkeypatch)
    proposer_auth = jwt_authorization("proposal-user", ["operator"])
    reviewer_auth = jwt_authorization("risk-reviewer", ["reviewer"])
    approvals = {
        "approval-self": authoritative_approval(
            approval_id="approval-self",
            reviewer="proposal-user",
        ),
        "approval-other-target": authoritative_approval(
            approval_id="approval-other-target",
            target_id="s-other",
        ),
        "approval-other-tenant": authoritative_approval(
            approval_id="approval-other-tenant",
            tenant_id="tenant-other",
        ),
    }
    monkeypatch.setattr(bff_main.read_store, "get_approval_decision", approvals.get)
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": proposer_auth, "Idempotency-Key": "approval-self-negative"},
        json=payload(),
    )
    pid = created.json()["data"]["proposal_id"]
    validated = validate_proposal(c, pid, created.headers["etag"], authorization=proposer_auth)
    validated_data = validated.json()["data"]
    for approval in approvals.values():
        approval.update({
            "proposal_id": pid,
            "proposal_revision": validated_data["revision"],
            "proposal_content_digest": validated_data["proposal_content_digest"],
            "validation_result_digest": validated_data["validation_result_digest"],
            "decided_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        })

    self_approval = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={
            "Authorization": jwt_authorization("proposal-user", ["approver"]),
            "If-Match": validated.headers["etag"],
        },
        json={
            "action": "approve",
            "reason": "same proposer actor",
            "approval_refs": ["approval-other-target"],
        },
    )
    assert self_approval.status_code == 403
    assert "self-approval" in self_approval.text

    proposer_approval_ref = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={"Authorization": reviewer_auth, "If-Match": validated.headers["etag"]},
        json={
            "action": "approve",
            "reason": "canonical record was decided by proposer",
            "approval_refs": ["approval-self"],
        },
    )
    assert proposer_approval_ref.status_code == 403
    assert "self-approval" in proposer_approval_ref.text

    wrong_target = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={"Authorization": reviewer_auth, "If-Match": validated.headers["etag"]},
        json={
            "action": "approve",
            "reason": "wrong target",
            "approval_refs": ["approval-other-target"],
        },
    )
    assert wrong_target.status_code == 422
    assert "target id mismatch" in wrong_target.text

    wrong_tenant = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={"Authorization": reviewer_auth, "If-Match": validated.headers["etag"]},
        json={
            "action": "approve",
            "reason": "cross-tenant decision id",
            "approval_refs": ["approval-other-tenant"],
        },
    )
    assert wrong_tenant.status_code == 422
    assert "scope mismatch" in wrong_tenant.text


def test_approval_accepts_matching_canonical_decision(monkeypatch):
    c = strict_client(monkeypatch)
    proposer_auth = jwt_authorization("proposal-user", ["operator"])
    reviewer_auth = jwt_authorization("risk-reviewer", ["reviewer"])
    record = bff_main.read_store._project_canonical_approval_decision(
        {
            "decision_id": "approval-risk-1",
            "decision": "approved",
            "decision_state": "decided",
            "target_type": "strategy_spec",
            "target_id": "s-1",
            "target_version": "v1",
            "tenant_id": "pantheon-dev",
            "owner_user_id": "proposal-owner",
            "actor_id": "risk-reviewer",
            "actor_role": "risk_owner",
            "decided_at": "2026-07-14T00:00:00Z",
        }
    )
    monkeypatch.setattr(
        bff_main.read_store,
        "get_approval_decision",
        lambda approval_id: record if approval_id == record["decision_id"] else None,
    )
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": proposer_auth, "Idempotency-Key": "approval-authority-positive"},
        json=payload(),
    )
    pid = created.json()["data"]["proposal_id"]
    validated = validate_proposal(c, pid, created.headers["etag"], authorization=proposer_auth)
    validated_data = validated.json()["data"]
    record.update({
        "proposal_id": pid,
        "proposal_revision": validated_data["revision"],
        "proposal_content_digest": validated_data["proposal_content_digest"],
        "validation_result_digest": validated_data["validation_result_digest"],
        "decided_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    })

    outside_user_scope = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={
            "Authorization": jwt_authorization(
                "outside-reviewer",
                ["reviewer"],
                user_id="different-owner",
            ),
            "If-Match": validated.headers["etag"],
        },
        json={
            "action": "approve",
            "reason": "wrong user-private scope",
            "approval_refs": [record["decision_id"]],
        },
    )
    assert outside_user_scope.status_code == 404

    approved = c.post(
        f"/bff/agora/proposals/{pid}/actions",
        headers={
            "Authorization": reviewer_auth,
            "If-Match": validated.headers["etag"],
        },
        json={
            "action": "approve",
            "reason": "canonical risk approval linked",
            "approval_refs": [record["decision_id"]],
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["state"] == "approved"
    assert approved.json()["data"]["audit"][-1]["approval_refs"] == [record["decision_id"]]


def test_approval_rejects_stale_content_validation_time_and_expiry_bindings(monkeypatch):
    c = strict_client(monkeypatch)
    proposer_auth = jwt_authorization("proposal-user", ["operator"])
    reviewer_auth = jwt_authorization("risk-reviewer", ["reviewer"])
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": proposer_auth, "Idempotency-Key": "approval-exact-binding"},
        json=payload(),
    )
    pid = created.json()["data"]["proposal_id"]
    validated = validate_proposal(c, pid, created.headers["etag"], authorization=proposer_auth)
    proposal = validated.json()["data"]
    base = authoritative_approval(
        proposal_id=pid,
        proposal_revision=proposal["revision"],
        proposal_content_digest=proposal["proposal_content_digest"],
        validation_result_digest=proposal["validation_result_digest"],
    )
    records = {
        "wrong-revision": {**base, "decision_id": "wrong-revision", "id": "wrong-revision", "proposal_revision": proposal["revision"] - 1},
        "wrong-content": {**base, "decision_id": "wrong-content", "id": "wrong-content", "proposal_content_digest": "stale"},
        "wrong-validation": {**base, "decision_id": "wrong-validation", "id": "wrong-validation", "validation_result_digest": "stale"},
        "pre-validation": {**base, "decision_id": "pre-validation", "id": "pre-validation", "decided_at": "2020-01-01T00:00:00Z"},
        "expired": {**base, "decision_id": "expired", "id": "expired", "expires_at": "2020-01-01T00:00:00Z"},
        "superseded": {**base, "decision_id": "superseded", "id": "superseded", "superseded_by": "newer"},
    }
    monkeypatch.setattr(bff_main.read_store, "get_approval_decision", records.get)
    expected = {
        "wrong-revision": "revision mismatch",
        "wrong-content": "content digest mismatch",
        "wrong-validation": "validation digest mismatch",
        "pre-validation": "after validation",
        "expired": "expired",
        "superseded": "superseded",
    }
    for approval_id, message in expected.items():
        response = c.post(
            f"/bff/agora/proposals/{pid}/actions",
            headers={"Authorization": reviewer_auth, "If-Match": validated.headers["etag"]},
            json={"action": "approve", "reason": "binding check", "approval_refs": [approval_id]},
        )
        assert response.status_code == 422, response.text
        assert message in response.text
