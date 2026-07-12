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
