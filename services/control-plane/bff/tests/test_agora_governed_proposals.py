from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import main as bff_main

HEADERS = {"Authorization": "Bearer proposal-user:operator", "Idempotency-Key": "pint-004-create"}


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


def error_payload(response):
    body = response.json()
    detail = body.get("detail", body)
    return detail["error"]


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


def test_same_proposer_cannot_approve_validated_proposal_and_no_revision_is_appended(monkeypatch):
    c = client(monkeypatch)
    authorization = "Bearer proposal-self:operator,approver"
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": authorization, "Idempotency-Key": "self-approval-create"},
        json=payload(),
    )
    assert created.status_code == 201, created.text
    proposal_id = created.json()["data"]["proposal_id"]
    validated = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": created.headers["etag"]},
        json={
            "action": "validate",
            "reason": "paper checks passed",
            "validation_result": {"status": "passed", "environment": "paper"},
        },
    )
    assert validated.status_code == 200, validated.text

    denied = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": validated.headers["etag"]},
        json={
            "action": "approve",
            "reason": "approve my own proposal",
            "approval_refs": ["invented-ref"],
        },
    )

    assert denied.status_code == 403, denied.text
    error = error_payload(denied)
    assert error["code"] == "FORBIDDEN"
    assert error["details"]["reason"] == "AGORA_PROPOSAL_SELF_APPROVAL_FORBIDDEN"
    assert error["details"]["precondition_failed"] == "distinct_approver"
    assert error["details"]["actorId"] == "proposal-self"
    assert error["details"]["proposerId"] == "proposal-self"

    latest = c.get(
        f"/bff/agora/proposals/{proposal_id}",
        headers={"Authorization": authorization},
    )
    history = c.get(
        f"/bff/agora/proposals/{proposal_id}/revisions",
        headers={"Authorization": authorization},
    )
    assert latest.status_code == history.status_code == 200
    assert latest.headers["etag"] == validated.headers["etag"]
    assert latest.json()["data"]["state"] == "validated"
    assert [row["revision"] for row in history.json()["data"]] == [1, 2]
    assert all(event["action"] != "approve" for event in latest.json()["data"]["audit"])


def test_validated_proposal_approve_requires_approver_role(monkeypatch):
    c = client(monkeypatch)
    authorization = "Bearer proposal-role:operator"
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": authorization, "Idempotency-Key": "role-check-create"},
        json=payload(),
    )
    proposal_id = created.json()["data"]["proposal_id"]
    validated = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": created.headers["etag"]},
        json={
            "action": "validate",
            "reason": "paper checks passed",
            "validation_result": {"status": "passed"},
        },
    )

    denied = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": validated.headers["etag"]},
        json={"action": "approve", "reason": "approve", "approval_refs": ["review-1"]},
    )

    assert denied.status_code == 403, denied.text
    assert error_payload(denied)["details"]["reason"] == "AGORA_PROPOSAL_APPROVER_ROLE_REQUIRED"
    latest = c.get(
        f"/bff/agora/proposals/{proposal_id}",
        headers={"Authorization": authorization},
    )
    assert latest.json()["data"]["revision"] == 2


def test_viewer_cannot_create_or_act_on_governed_proposal(monkeypatch):
    c = client(monkeypatch)
    viewer_create = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": "Bearer proposal-viewer:viewer", "Idempotency-Key": "viewer-create"},
        json=payload(),
    )
    assert viewer_create.status_code == 403

    owner_auth = "Bearer proposal-owner:operator"
    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": owner_auth, "Idempotency-Key": "viewer-act-owner"},
        json=payload(),
    )
    proposal_id = created.json()["data"]["proposal_id"]
    viewer_act = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={
            "Authorization": "Bearer proposal-owner:viewer",
            "If-Match": created.headers["etag"],
        },
        json={"action": "modify", "reason": "viewer write", "proposed_value": {"risk": .04}},
    )

    assert viewer_act.status_code == 403
    latest = c.get(
        f"/bff/agora/proposals/{proposal_id}",
        headers={"Authorization": owner_auth},
    )
    assert latest.json()["data"]["revision"] == 1


def test_governance_errors_use_pack_d_codes_and_reasons(monkeypatch):
    c = client(monkeypatch)
    authorization = "Bearer proposal-errors:operator"

    missing = c.get(
        "/bff/agora/proposals/prop_missing",
        headers={"Authorization": authorization},
    )
    assert missing.status_code == 404
    assert error_payload(missing)["code"] == "RESOURCE_NOT_FOUND"
    assert error_payload(missing)["details"]["reason"] == "AGORA_PROPOSAL_NOT_FOUND"

    created = c.post(
        "/bff/agora/proposals",
        headers={"Authorization": authorization, "Idempotency-Key": "error-shapes-create"},
        json=payload(),
    )
    proposal_id = created.json()["data"]["proposal_id"]
    stale = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": '"stale"'},
        json={"action": "modify", "reason": "stale", "proposed_value": {"risk": .05}},
    )
    assert stale.status_code == 412
    assert error_payload(stale)["code"] == "PRECONDITION_FAILED"
    assert error_payload(stale)["details"]["reason"] == "AGORA_PROPOSAL_ETAG_STALE"

    premature = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": created.headers["etag"]},
        json={"action": "approve", "reason": "premature"},
    )
    assert premature.status_code == 422
    assert error_payload(premature)["code"] == "VALIDATION_FAILED"
    assert error_payload(premature)["details"]["reason"] == "AGORA_PROPOSAL_APPROVAL_PRECONDITION_FAILED"

    rejected = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": created.headers["etag"]},
        json={"action": "reject", "reason": "risk rejected"},
    )
    terminal = c.post(
        f"/bff/agora/proposals/{proposal_id}/actions",
        headers={"Authorization": authorization, "If-Match": rejected.headers["etag"]},
        json={"action": "modify", "reason": "too late", "proposed_value": {"risk": .03}},
    )
    assert terminal.status_code == 409
    assert error_payload(terminal)["code"] == "RESOURCE_CONFLICT"
    assert error_payload(terminal)["details"]["reason"] == "AGORA_PROPOSAL_TERMINAL_STATE"


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
