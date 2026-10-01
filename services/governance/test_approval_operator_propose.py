"""Operator proposal authority and PROPOSED->decide in one owner CAS callback.

Runs the mounted approval routes against an in-memory store that mirrors
``PostgresApprovalDecisionStore.execute_command`` (tenant 404, expected_version
CAS, idempotent receipt replay), so it needs no database.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile

import pytest

os.environ.setdefault("GOVERNANCE_DATA_DIR", tempfile.mkdtemp(prefix="gov_operator_"))
from fastapi.testclient import TestClient  # noqa: E402

from services.governance import main  # noqa: E402
from services.governance.pg_store import ApprovalCommandConflict, ApprovalCommandNotFound  # noqa: E402
from services.governance.test_approval_authority_postgres import token  # noqa: E402
from approval_decision import ApprovalDecision  # type: ignore  # noqa: E402

SECRET = "s" * 40
TENANT = "tenant-op"
SUBJECT = {"binding_id": "b1", "persona_id": "p1", "capital_pool_id": "pool1", "risk_direction": "increase"}


class MemoryCommandStore:
    def __init__(self):
        self.rows, self.receipts = {}, {}

    def get(self, decision_id):
        row = self.rows.get(decision_id)
        return ApprovalDecision.from_dict(copy.deepcopy(row)) if row else None

    def list_all(self):
        return [self.get(key) for key in self.rows]

    def find_by_target(self, target_type, target_id):
        return [d for d in self.list_all() if d.target_id == target_id]

    def execute_command(self, *, command, mutate, audit_store):
        digest = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
        key = (command["tenant_id"], command["actor_id"], command["idempotency_key"])
        if key in self.receipts:
            if self.receipts[key][0] != digest:
                raise ApprovalCommandConflict("Idempotency key has different command content")
            return self.receipts[key][1]
        base = self.rows.get(command["decision_id"])
        if base and base["tenant_id"] != command["tenant_id"]:
            raise ApprovalCommandNotFound("Approval decision not found")
        if (base.get("version", 0) if base else 0) != command["expected_version"]:
            raise ApprovalCommandConflict("Approval base version is stale")
        decision = mutate(ApprovalDecision.from_dict(copy.deepcopy(base)) if base else None)
        decision.version = command["expected_version"] + 1
        decision.event_id = f"evt-{decision.version}"
        self.rows[decision.decision_id] = payload = decision.to_dict()
        self.receipts[key] = (digest, payload)
        return payload


@pytest.fixture()
def owner(monkeypatch):
    for name, value in {"JWT_SECRET": SECRET, "JWT_ISSUER": "iss", "JWT_AUDIENCE": "aud"}.items():
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_{name}", value)
    monkeypatch.setattr(main, "store", MemoryCommandStore())
    return TestClient(main.app)


def auth(sub, role, tenant=TENANT, key=None):
    result = {"Authorization": "Bearer " + token(SECRET, sub=sub, roles=[role], tenant_id=tenant, iss="iss", aud="aud")}
    if key:
        result["Idempotency-Key"] = key
    return result


def proposal(**overrides):
    body = dict(target_type="capital_binding_activation", target_id="b1", target_version="1", risk_level="medium",
                tenant_id=TENANT, owner_user_id="op-1", expected_version=0, subject=SUBJECT, decision_id="d1")
    body.update(overrides)
    return body


def vote(sub, role, version, outcome="approved", key="k", held=None):
    return {"json": {"expected_version": version, "actor_role": role, "actor_id": sub, "outcome": outcome,
                     "rationale": "reviewed", "expires_at": "2099-01-01T00:00:00Z"},
            "headers": auth(sub, held or role, key=key)}


def test_operator_proposes_and_reads_only_within_own_tenant_and_owner(owner):
    assert owner.post("/api/governance/approvals", json=proposal(), headers=auth("op-1", "operator", key="p")).status_code == 201
    assert owner.get("/api/governance/approvals/d1", headers=auth("op-1", "operator")).json()["version"] == 1
    assert owner.get("/api/governance/approvals/d1", headers=auth("op-2", "operator", tenant="other")).status_code == 404
    assert owner.post("/api/governance/approvals", json=proposal(decision_id="d2", owner_user_id="op-2"),
                      headers=auth("op-1", "operator", key="q")).status_code == 403
    assert owner.post("/api/governance/approvals", json=proposal(decision_id="d3", tenant_id="other"),
                      headers=auth("op-1", "operator", key="r")).status_code == 403


def test_operator_cannot_decide_and_proposer_cannot_vote(owner):
    owner.post("/api/governance/approvals", json=proposal(owner_user_id="rev-1"),
               headers=auth("rev-1", "operator", key="p"))
    assert owner.post("/api/governance/approvals/d1/decide", **vote("op-1", "governance_reviewer", 1, held="operator")).status_code == 403
    denied = owner.post("/api/governance/approvals/d1/decide", **vote("rev-1", "governance_reviewer", 1))
    assert denied.status_code == 400 and "proposer" in denied.text


def test_two_distinct_voters_complete_target_and_first_vote_stays_under_review(owner):
    owner.post("/api/governance/approvals", json=proposal(), headers=auth("op-1", "operator", key="p"))
    first = owner.post("/api/governance/approvals/d1/decide", **vote("rev-1", "governance_reviewer", 1, key="v1"))
    assert first.status_code == 200, first.text
    assert first.json()["decision_state"] == "under_review" and first.json()["version"] == 2
    stale = owner.post("/api/governance/approvals/d1/decide", **vote("rk-1", "risk_owner", 1, key="v2"))
    assert stale.status_code == 409
    replay = owner.post("/api/governance/approvals/d1/decide", **vote("rev-1", "governance_reviewer", 1, key="v1"))
    assert replay.json() == first.json()
    second = owner.post("/api/governance/approvals/d1/decide", **vote("rk-1", "risk_owner", 2, key="v3"))
    assert second.status_code == 200 and second.json()["decision_state"] == "decided" and second.json()["version"] == 3
